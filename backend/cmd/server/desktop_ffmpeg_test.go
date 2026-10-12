package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRewriteH264Args(t *testing.T) {
	in := []string{"-i", "a.mp4", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "out.mp4"}
	if got := strings.Join(rewriteH264Args(in, "libx264"), " "); got != strings.Join(in, " ") {
		t.Fatalf("libx264 should be untouched: %q", got)
	}
	got := strings.Join(rewriteH264Args(in, "libopenh264"), " ")
	want := "-i a.mp4 -c:v libopenh264 -c:a aac -b:v 6M out.mp4"
	if got != want {
		t.Fatalf("got %q\nwant %q", got, want)
	}
	copyArgs := []string{"-i", "a.mp4", "-c", "copy", "out.mp4"}
	if got := strings.Join(rewriteH264Args(copyArgs, "h264_mf"), " "); got != "-i a.mp4 -c copy out.mp4" {
		t.Fatalf("non-x264 args changed: %q", got)
	}
}

func TestValidateDesktopFFmpegArgs(t *testing.T) {
	dir := t.TempDir()
	files := map[string]bool{"input-0.mp4": true, "input-1.mp4": true, "concat.txt": true}
	write := func(content string) {
		if err := os.WriteFile(filepath.Join(dir, "concat.txt"), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write("file 'input-0.mp4'\nfile 'input-1.mp4'\n")
	ok := [][]string{
		{"-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", "merged.mp4"},
		{"-i", "input-0.mp4", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "out.mp4"},
		{"-i", "input-0.mp4", "-vf", "subtitles=subtitles.srt", "out.mp4"},
	}
	for _, args := range ok {
		if _, err := validateDesktopFFmpegArgs(args, dir, files); err != nil {
			t.Errorf("%v rejected: %v", args, err)
		}
	}
	bad := [][]string{
		{"-i", "C:/Windows/win.ini", "out.mp4"},
		{"-i", "http://example.com/a.mp4", "out.mp4"},
		{"-i", "input-0.mp4", "../escape.mp4"},
		{"-i", "input-0.mp4", "/tmp/out.mp4"},
		{"-i", "missing.mp4", "out.mp4"},
		{"-i", "input-0.mp4", "input-1.mp4"},
		{"-f", "lavfi", "-i", "input-0.mp4", "-vf", "movie=secret.mp4", "out.mp4"},
		{"-i", "input-0.mp4", "-vf", "subtitles=D:\\secret.srt", "out.mp4"},
	}
	for _, args := range bad {
		if _, err := validateDesktopFFmpegArgs(args, dir, files); err == nil {
			t.Errorf("%v should be rejected", args)
		}
	}
	write("file 'C:/secret.mp4'\n")
	if _, err := validateDesktopFFmpegArgs(ok[0], dir, files); err == nil {
		t.Error("concat list referencing outside file should be rejected")
	}
}
