package protocol

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestZaowuLocalTaskLifecycle(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "..", "plugin-packages", "zaowu-local", "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	adapters, err := LoadInstalledProviders(data, nil)
	if err != nil || len(adapters) != 4 {
		t.Fatalf("load local providers: %v, count %d", err, len(adapters))
	}
	for _, adapter := range adapters {
		t.Run(adapter.Metadata().ID, func(t *testing.T) {
			spec, err := adapter.BuildCreate(context.Background(), RequestContext{Request: GenerationRequest{
				Model: "fixture", Prompt: "fixture", Duration: 5, AspectRatio: "16:9",
				Quality: "1080p", Resolution: "720p",
				Images: []MediaReference{{DataURL: "data:image/png;base64,fixture", URL: "https://example.com/ref.png"}},
				Extra:  map[string]any{"idempotencyKey": "fixture-idempotency"},
			}})
			if err != nil {
				t.Fatal(err)
			}
			if spec.Path != "/api/v1/tasks" || !spec.OriginPath || spec.Headers["Idempotency-Key"] != "fixture-idempotency" || spec.Auth.Type != "bearer" {
				t.Fatalf("incorrect create transport: %#v", spec)
			}
			body := spec.Body.(map[string]any)
			if body["model"] != "fixture" || body["prompt"] != "fixture" {
				t.Fatalf("incorrect body: %#v", body)
			}
			pollContext := PollContext{TaskID: "task-1"}
			poll, err := adapter.BuildPoll(context.Background(), pollContext)
			if err != nil || poll.Path != "/api/v1/tasks/task-1" || poll.Auth.Type != "bearer" {
				t.Fatalf("incorrect poll: %#v, %v", poll, err)
			}
			for _, value := range []string{"queued", "preparing", "submitting", "submitted", "processing", "downloading"} {
				state, err := adapter.ParsePoll(context.Background(), pollContext, []byte(`{"id":"task-1","status":"`+value+`"}`))
				if err != nil || (state.Status != StatusPending && state.Status != StatusProcessing) {
					t.Fatalf("live task state %s became terminal: %#v, %v", value, state, err)
				}
			}
			for _, value := range []string{"failed", "poll_failed", "download_failed", "submission_unknown"} {
				state, err := adapter.ParsePoll(context.Background(), pollContext, []byte(`{"id":"task-1","status":"`+value+`","error":"fixture failure"}`))
				if err != nil || state.Status != StatusFailed || state.Message != "fixture failure" {
					t.Fatalf("incorrect failure mapping %s: %#v, %v", value, state, err)
				}
			}
			state, err := adapter.ParsePoll(context.Background(), pollContext, []byte(`{"id":"task-1","status":"completed","result_ready":true}`))
			if err != nil || state.Status != StatusSucceeded {
				t.Fatalf("incorrect completed mapping: %#v, %v", state, err)
			}
			result, err := adapter.(ResultAdapter).BuildResult(context.Background(), pollContext)
			if err != nil || result.Path != "/api/v1/tasks/task-1/result" || !result.OriginPath || result.Auth.Type != "bearer" {
				t.Fatalf("incorrect authenticated result download: %#v, %v", result, err)
			}
		})
	}
}
