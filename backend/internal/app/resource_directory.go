package app

import (
	"errors"
	"os"
	"path/filepath"
)

// ConfigureLocalResourceDirectory runs before workers start. Databases,
// credentials and staging files remain in the backend data directory.
func (s *Service) ConfigureLocalResourceDirectory(directory string) error {
	if directory == "" {
		return nil
	}
	if !filepath.IsAbs(directory) {
		return errors.New("本地素材目录必须是绝对路径")
	}
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return err
	}
	probe, err := os.CreateTemp(directory, ".zaowu-write-")
	if err != nil {
		return err
	}
	name := probe.Name()
	closeErr := probe.Close()
	removeErr := os.Remove(name)
	if closeErr != nil {
		return closeErr
	}
	if removeErr != nil {
		return removeErr
	}
	s.resourceDir = filepath.Clean(directory)
	return nil
}

func (s *Service) localResourceRoot() string {
	if s.resourceDir != "" {
		return s.resourceDir
	}
	return filepath.Join(s.dataDir, "resources")
}
