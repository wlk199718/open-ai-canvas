package app

import (
	"bytes"
	"image"
	"image/jpeg"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"yingce/backend/internal/model"
)

func TestGeneratedJPEGCorrectsMislabeledPNGAndHydratesHistoricalResource(t *testing.T) {
	s, db := newMediaRecoveryTestService(t)
	var encoded bytes.Buffer
	if err := jpeg.Encode(&encoded, image.NewRGBA(image.Rect(0, 0, 2, 3)), nil); err != nil {
		t.Fatal(err)
	}
	pixels := encoded.Bytes()
	stored, err := s.persistGeneratedMediaResult("mime-user", map[string]interface{}{
		"images": []interface{}{map[string]interface{}{"dataUrl": dataURL("image/png", pixels)}},
	})
	if err != nil {
		t.Fatal(err)
	}
	output := stored["images"].([]interface{})[0].(map[string]interface{})
	resource, err := s.repo.ResourceForUser("mime-user", output["resourceId"].(string))
	if err != nil {
		t.Fatal(err)
	}
	if output["mimeType"] != "image/jpeg" || resource.MimeType != "image/jpeg" || !strings.HasSuffix(resource.ObjectKey, ".jpg") {
		t.Fatal("generated JPEG was saved with incorrect metadata or extension")
	}
	_, body, err := s.OpenResource("mime-user", resource.ID)
	if err != nil {
		t.Fatal(err)
	}
	actual, readErr := io.ReadAll(body)
	_ = body.Close()
	if readErr != nil || !bytes.Equal(actual, pixels) {
		t.Fatal("generated image bytes changed")
	}
	// Reproduce an existing PNG-labeled JPEG without changing the source bytes.
	oldKey := strings.TrimSuffix(resource.ObjectKey, ".jpg") + ".png"
	if err := os.Rename(filepath.Join(s.dataDir, "resources", filepath.FromSlash(resource.ObjectKey)), filepath.Join(s.dataDir, "resources", filepath.FromSlash(oldKey))); err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&model.Resource{}).Where("id = ?", resource.ID).Updates(map[string]interface{}{"mime_type": "image/png", "object_key": oldKey}).Error; err != nil {
		t.Fatal(err)
	}
	for _, inline := range []bool{false, true} {
		media := providerMedia{StorageKey: "resource:" + resource.ID, MimeType: "image/png", Type: "image/png"}
		if inline {
			media.DataURL = dataURL("image/png", pixels)
		}
		if err := s.hydrateProviderMedia("mime-user", &media, providerMediaHydrationPolicy{}); err != nil {
			t.Fatal(err)
		}
		if media.MimeType != "image/jpeg" || media.DataURL != dataURL("image/jpeg", pixels) {
			t.Fatal("historical reference did not receive a JPEG data URL")
		}
	}
	foreign := providerMedia{StorageKey: "resource:" + resource.ID, DataURL: dataURL("image/png", pixels)}
	if err := s.hydrateProviderMedia("other-user", &foreign, providerMediaHydrationPolicy{}); err == nil {
		t.Fatal("another user's reference was accepted")
	}
	media := providerMedia{DataURL: dataURL("image/png", pixels)}
	if err := s.hydrateProviderMedia("mime-user", &media, providerMediaHydrationPolicy{}); err != nil || media.DataURL != dataURL("image/jpeg", pixels) {
		t.Fatal("inline-only JPEG retained its PNG label")
	}
}

func TestNormalizedMediaMimeTypeUsesImageSignature(t *testing.T) {
	for _, test := range []struct {
		name, declared, expected string
		data                     []byte
	}{
		{"jpeg", "image/png", "image/jpeg", []byte{0xff, 0xd8, 0xff, 0xe0}},
		{"png", "image/jpeg", "image/png", []byte("\x89PNG\r\n\x1a\n")},
		{"webp", "image/png", "image/webp", []byte("RIFF\x00\x00\x00\x00WEBPVP8 ")},
		{"unknown codec", "video/mp4", "video/mp4", []byte("fixture")},
	} {
		t.Run(test.name, func(t *testing.T) {
			if got := normalizedMediaMimeType(test.declared, test.data); got != test.expected {
				t.Fatalf("MIME = %q, want %q", got, test.expected)
			}
		})
	}
}
