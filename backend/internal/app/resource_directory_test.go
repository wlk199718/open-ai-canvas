package app

import (
	"bytes"
	"io"
	"os"
	"path/filepath"
	"testing"

	"yingce/backend/internal/model"
)

func TestConfiguredResourceDirectoryLifecycle(t *testing.T) {
	data := t.TempDir()
	root := t.TempDir()
	s := &Service{dataDir: data}
	if err := s.ConfigureLocalResourceDirectory(filepath.Join(root, "assets")); err != nil {
		t.Fatal(err)
	}
	key := "users/fixture/image/2026/10/12/generated.png"
	resource := &model.Resource{Provider: "local", ObjectKey: key, Status: model.ResourceStatusReady, Size: 7}
	if _, err := s.storeTaskMediaObject(resource, "generated.png", bytes.NewBufferString("fixture")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(data, "resources", filepath.FromSlash(key))); !os.IsNotExist(err) {
		t.Fatalf("media must not be written beside the database: %v", err)
	}
	stream, err := s.openResourceRange("fixture", resource, "")
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(stream.Body)
	stream.Body.Close()
	if err != nil || string(body) != "fixture" {
		t.Fatalf("read migrated media: %s, %v", body, err)
	}
	if _, err := s.storeResourceObject(resource, "generated.png", bytes.NewBufferString("updated")); err != nil {
		t.Fatal(err)
	}
	sibling := filepath.Join(root, "provider.mp4")
	if err := os.WriteFile(sibling, []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := s.deleteLocalResourceObject("../provider.mp4"); err == nil {
		t.Fatal("deletion must reject files outside the configured resource directory")
	}
	if err := s.deleteLocalResourceObject(key); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(s.localResourceRoot(), filepath.FromSlash(key))); !os.IsNotExist(err) {
		t.Fatalf("configured resource was not deleted: %v", err)
	}
	if _, err := os.Stat(sibling); err != nil {
		t.Fatalf("provider sibling must remain: %v", err)
	}
}

func TestResourceDirectoryRejectsInvalidConfiguration(t *testing.T) {
	s := &Service{dataDir: t.TempDir()}
	original := s.localResourceRoot()
	if err := s.ConfigureLocalResourceDirectory("relative"); err == nil {
		t.Fatal("relative resource roots must fail")
	}
	file := filepath.Join(t.TempDir(), "file")
	os.WriteFile(file, []byte("fixture"), 0o600)
	if err := s.ConfigureLocalResourceDirectory(file); err == nil {
		t.Fatal("a file cannot be configured as a resource root")
	}
	if s.localResourceRoot() != original {
		t.Fatal("failed configuration must preserve the current root")
	}
}
