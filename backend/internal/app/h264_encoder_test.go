package app

import (
	"strings"
	"testing"
)

func TestH264ArgsFor(t *testing.T) {
	if got := strings.Join(h264ArgsFor("libx264"), " "); got != "-c:v libx264 -preset veryfast -crf 23" {
		t.Fatalf("libx264 args = %q", got)
	}
	got := strings.Join(h264ArgsFor("h264_mf"), " ")
	if got != "-c:v h264_mf -b:v 6M" {
		t.Fatalf("h264_mf args = %q", got)
	}
	if strings.Contains(got, "crf") || strings.Contains(got, "preset") {
		t.Fatalf("fallback encoder must not receive libx264-only options: %q", got)
	}
}
