package app

import (
	"errors"
	"strings"
	"testing"

	"yingce/backend/internal/model"
)

func TestPublicMediaURLRequiredError(t *testing.T) {
	cause := errors.New("no base url")
	err := publicMediaURLRequiredError(cause)
	if err.Reason != ReasonPublicMediaURLRequired || err.Status != 400 {
		t.Fatalf("unexpected reason/status: %q %d", err.Reason, err.Status)
	}
	if !errors.Is(err, cause) {
		t.Fatal("cause not preserved")
	}
	if !strings.Contains(err.Error(), "我的对象存储") {
		t.Fatalf("message lacks settings guidance: %s", err.Error())
	}
}

func TestPublicMediaURLUnavailable(t *testing.T) {
	local := &model.Resource{Provider: "local"}
	oss := &model.Resource{Provider: "aliyun"}
	private := NewAppError(503, "private")
	private.Reason = "resource_origin_private"
	other := NewAppError(503, "signing failed")
	other.Reason = "resource_signing_failed"
	noBase := NewAppError(400, "no base")
	noBase.Reason = reasonPublicBaseURLUnconfigured

	cases := []struct {
		name     string
		resource *model.Resource
		err      error
		want     bool
	}{
		{"local without public base url", local, noBase, true},
		{"local disk error keeps original error", local, errors.New("sync: Access is denied"), false},
		{"local signing failure keeps original error", local, other, false},
		{"oss private origin", oss, private, true},
		{"oss signing failure keeps original error", oss, other, false},
		{"oss plain error", oss, errors.New("boom"), false},
	}
	for _, c := range cases {
		if got := publicMediaURLUnavailable(c.resource, c.err); got != c.want {
			t.Errorf("%s: got %v want %v", c.name, got, c.want)
		}
	}
}

func TestHydrateInlineDataRejectedWithGuidance(t *testing.T) {
	s := &Service{}
	media := providerMedia{DataURL: "data:image/png;base64,AAAA"}
	err := s.hydrateProviderMedia("user", &media, providerMediaHydrationPolicy{requireURL: true})
	var appErr *AppError
	if !errors.As(err, &appErr) || appErr.Reason != ReasonPublicMediaURLRequired {
		t.Fatalf("expected public_media_url_required, got %v", err)
	}
}
