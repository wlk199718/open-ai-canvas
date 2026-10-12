package database

import "testing"

func TestTranslateSQLiteDSN(t *testing.T) {
	cases := map[string]string{
		"data/x.db": "data/x.db",
		"data/x.db?_busy_timeout=5000&_journal_mode=WAL&_foreign_keys=on&_synchronous=NORMAL": "data/x.db?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)&_pragma=foreign_keys(1)&_pragma=synchronous(NORMAL)",
		"file:x.db?mode=ro&_busy_timeout=5000":                                                 "file:x.db?mode=ro&_pragma=busy_timeout(5000)",
		"x.db?_fk=off": "x.db?_pragma=foreign_keys(0)",
	}
	for in, want := range cases {
		if got := translateSQLiteDSN(in); got != want {
			t.Errorf("translateSQLiteDSN(%q)\n got  %q\n want %q", in, got, want)
		}
	}
}

func TestOpenSQLitePragmasApplied(t *testing.T) {
	db, err := Open(Config{Driver: "sqlite", DataDir: t.TempDir()})
	if err != nil {
		t.Fatal(err)
	}
	sqlDB, err := db.DB()
	if err != nil {
		t.Fatal(err)
	}
	// Windows 下文件句柄未关闭会导致 TempDir 清理失败
	t.Cleanup(func() { _ = sqlDB.Close() })
	var mode string
	var fk int
	db.Raw("PRAGMA journal_mode").Scan(&mode)
	db.Raw("PRAGMA foreign_keys").Scan(&fk)
	if mode != "wal" || fk != 1 {
		t.Fatalf("pragmas not applied: journal_mode=%q foreign_keys=%d", mode, fk)
	}
}
