package app

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"yingce/backend/internal/model"
	"yingce/backend/internal/platform"
)

func TestFailedImageProviderRecoveryDownloadsExistingTaskWithoutResubmitting(t *testing.T) {
	t.Setenv("CANVAS_ALLOWED_PRIVATE_UPSTREAM_HOSTS", "127.0.0.1")
	s, db := newMediaRecoveryTestService(t)
	s.coordinator = platform.NewCoordinatorWithRedis(nil, "image-recovery-test")
	center, err := newPluginRuntime(s.dataDir)
	if err != nil {
		t.Fatal(err)
	}
	s.pluginRuntime = center
	picture := mediaTestPNG(t)
	creates, polls, downloads := 0, 0, 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer fixture-token" {
			t.Error("missing recovery authorization")
		}
		switch {
		case r.Method != http.MethodGet:
			creates++
			http.Error(w, "must not generate again", http.StatusConflict)
		case r.URL.Path == "/api/v1/tasks/existing-image":
			polls++
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"id":"existing-image","status":"completed","result_ready":true}`))
		case r.URL.Path == "/api/v1/tasks/existing-image/result":
			downloads++
			w.Header().Set("Content-Type", "image/png")
			_, _ = w.Write(picture)
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	task := seedMediaTask(t, db, providerConfig{
		BaseURL: server.URL, APIKey: "fixture-token", Model: "nano2 lite",
		InterfaceType: "zaowu-local-image", APIFormat: "openai",
	})
	if err := db.Model(task).Updates(map[string]any{
		"status": model.TaskStatusFailed, "error": "upstream status was misclassified",
		"provider_request_id": "existing-image", "lease_owner": "", "lease_expires_at": nil,
	}).Error; err != nil {
		t.Fatal(err)
	}
	if _, err := s.QueryFailedVideoTask(context.Background(), "other-user", task.ID); err == nil {
		t.Fatal("recovery accepted another user's task")
	}
	if polls != 0 || downloads != 0 || creates != 0 {
		t.Fatal("unauthorized recovery called upstream")
	}
	result, err := s.QueryFailedVideoTask(context.Background(), task.UserID, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Recovered || result.Task.Status != model.TaskStatusSucceeded || result.Task.Error != "" {
		t.Fatalf("image recovery result = %+v", result)
	}
	if creates != 0 || polls != 1 || downloads != 1 || result.Task.ProviderRequestID != "existing-image" {
		t.Fatalf("recovery calls create=%d poll=%d download=%d task=%+v", creates, polls, downloads, result.Task)
	}
}

func TestProviderTaskRecoveryContextSurvivesClientCancellation(t *testing.T) {
	type contextKey string
	parent, cancelParent := context.WithCancel(context.WithValue(context.Background(), contextKey("trace"), "trace-1"))
	cancelParent()

	recovery, cancelRecovery := providerTaskRecoveryContext(parent)
	defer cancelRecovery()
	if recovery.Err() != nil {
		t.Fatalf("recovery context inherited cancellation: %v", recovery.Err())
	}
	if recovery.Value(contextKey("trace")) != "trace-1" {
		t.Fatal("recovery context did not preserve request values")
	}
	if _, ok := recovery.Deadline(); !ok {
		t.Fatal("recovery context has no bounded deadline")
	}
}

func TestSettleRecoveredBillingIsIdempotent(t *testing.T) {
	tests := []struct {
		name        string
		status      model.BillingStatus
		wantSettle  int
		wantRestore int
	}{
		{name: "settled", status: model.BillingStatusSettled},
		{name: "running", status: model.BillingStatusRunning, wantSettle: 1},
		{name: "refunded", status: model.BillingStatusRefunded, wantRestore: 1},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			repo := &taskBillingRepositoryStub{order: &model.BillingOrder{Status: tt.status}}
			svc := &Service{taskBillingCoordinator: newTaskBillingCoordinator(repo)}
			if err := svc.settleRecoveredBilling(&model.Task{BillingOrderID: "order-1"}, "provider-1"); err != nil {
				t.Fatalf("settleRecoveredBilling() error = %v", err)
			}
			if got := len(repo.settleCalls); got != tt.wantSettle {
				t.Fatalf("settle calls = %d, want %d", got, tt.wantSettle)
			}
			if got := len(repo.restoreCalls); got != tt.wantRestore {
				t.Fatalf("restore calls = %d, want %d", got, tt.wantRestore)
			}
		})
	}
}

func TestDownloadRemoteResourceEnforcesVideoImportBounds(t *testing.T) {
	t.Setenv("CANVAS_ALLOW_PRIVATE_UPSTREAMS", "true")
	t.Setenv("HTTP_PROXY", "")
	t.Setenv("HTTPS_PROXY", "")
	t.Setenv("ALL_PROXY", "")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/large" {
			w.Header().Set("Content-Type", "video/mp4")
			_, _ = w.Write([]byte("12345"))
			return
		}
		w.Header().Set("Content-Type", "video/mp4; charset=binary")
		_, _ = w.Write([]byte("video"))
	}))
	defer server.Close()

	payload, err := downloadRemoteResource(server.URL+"/video.mp4", 16)
	if err != nil {
		t.Fatalf("downloadRemoteResource() error = %v", err)
	}
	if payload.mimeType != "video/mp4" || string(payload.data) != "video" {
		t.Fatalf("payload = %#v, want video/mp4 body", payload)
	}
	if _, err := downloadRemoteResource(server.URL+"/large", 5); err == nil || !strings.Contains(err.Error(), "远程资源必须小于") {
		t.Fatalf("oversized download error = %v", err)
	}
	if _, err := downloadRemoteResource("file:///tmp/video.mp4", 16); err == nil {
		t.Fatal("downloadRemoteResource() accepted a non-http URL")
	}
}

func TestAdminVideoRecoveryRequiresAdmin(t *testing.T) {
	service := &Service{}
	methods := map[string]func(*model.User) error{
		"query": func(actor *model.User) error {
			_, err := service.AdminQueryFailedVideoTask(context.Background(), actor, "log-1", "provider-1")
			return err
		},
		"url": func(actor *model.User) error {
			_, err := service.AdminRecoverVideoByURL(context.Background(), actor, "log-1", "https://example.com/video.mp4", "provider-1")
			return err
		},
		"batch": func(actor *model.User) error {
			_, err := service.AdminBatchQueryFailedVideoTasks(context.Background(), actor, []string{"log-1"})
			return err
		},
	}
	actors := map[string]*model.User{
		"anonymous":     nil,
		"ordinary user": {Role: model.UserRoleUser},
	}
	for actorName, actor := range actors {
		for methodName, invoke := range methods {
			t.Run(actorName+"/"+methodName, func(t *testing.T) {
				if err := invoke(actor); err == nil {
					t.Fatalf("recovery method %q accepted %s actor", methodName, actorName)
				}
			})
		}
	}
}

func TestRetryableProtocolMediaDownload(t *testing.T) {
	for _, err := range []error{
		errors.New("net/http: TLS handshake timeout"),
		errors.New("read: connection reset by peer"),
		errors.New("unexpected EOF"),
	} {
		if !retryableProtocolMediaDownload(err) {
			t.Fatalf("retryableProtocolMediaDownload(%v) = false", err)
		}
	}
	for _, err := range []error{context.Canceled, context.DeadlineExceeded, errors.New("HTTP 404")} {
		if retryableProtocolMediaDownload(err) {
			t.Fatalf("retryableProtocolMediaDownload(%v) = true", err)
		}
	}
}
