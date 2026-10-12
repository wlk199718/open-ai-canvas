package database

import (
	"errors"
	"fmt"
	"os"
	"strings"

	"yingce/backend/internal/logging"

	"github.com/jackc/pgx/v5/stdlib"
	// 纯 Go SQLite 驱动（modernc），无需 CGO，便于桌面端跨平台打包。
	"github.com/glebarez/sqlite"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
)

type Config struct {
	Driver  string
	DSN     string
	DataDir string
}

func Open(config Config) (*gorm.DB, error) {
	driver := strings.ToLower(strings.TrimSpace(config.Driver))
	if driver == "" {
		driver = "sqlite"
	}
	switch driver {
	case "sqlite":
		dsn := strings.TrimSpace(config.DSN)
		if dsn == "" {
			if err := os.MkdirAll(config.DataDir, 0o755); err != nil {
				return nil, err
			}
			dsn = config.DataDir + "/open_ai_canvas.db?_busy_timeout=5000&_journal_mode=WAL&_foreign_keys=on&_synchronous=NORMAL"
		}
		return gorm.Open(sqlite.Open(translateSQLiteDSN(dsn)), &gorm.Config{Logger: logging.GormLogger()})
	case "postgres", "postgresql":
		dsn := strings.TrimSpace(config.DSN)
		if dsn == "" {
			return nil, errors.New("PostgreSQL 模式必须配置 DATABASE_URL")
		}
		return gorm.Open(postgres.New(postgres.Config{
			DSN:          dsn,
			OptionOpenDB: []stdlib.OptionOpenDB{stdlib.OptionBeforeConnect(configurePostgresConnection)},
		}), &gorm.Config{Logger: logging.GormLogger()})
	default:
		return nil, fmt.Errorf("不支持的数据库驱动：%s", driver)
	}
}

// translateSQLiteDSN 把 mattn/go-sqlite3 风格参数（_busy_timeout=5000 等）
// 转成 modernc 驱动识别的 _pragma=name(value) 形式，保持既有 DSN 配置兼容。
func translateSQLiteDSN(dsn string) string {
	base, query, found := strings.Cut(dsn, "?")
	if !found || query == "" {
		return dsn
	}
	pragmaNames := map[string]string{
		"_busy_timeout": "busy_timeout",
		"_timeout":      "busy_timeout",
		"_journal_mode": "journal_mode",
		"_journal":      "journal_mode",
		"_foreign_keys": "foreign_keys",
		"_fk":           "foreign_keys",
		"_synchronous":  "synchronous",
		"_sync":         "synchronous",
	}
	parts := strings.Split(query, "&")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		key, value, _ := strings.Cut(part, "=")
		name, ok := pragmaNames[strings.ToLower(key)]
		if !ok {
			out = append(out, part)
			continue
		}
		if name == "foreign_keys" {
			switch strings.ToLower(value) {
			case "on", "true", "yes", "1":
				value = "1"
			default:
				value = "0"
			}
		}
		out = append(out, "_pragma="+name+"("+value+")")
	}
	return base + "?" + strings.Join(out, "&")
}

func ConfigurePool(db *gorm.DB) error {
	sqlDB, err := db.DB()
	if err != nil {
		return err
	}
	if db.Dialector.Name() == "postgres" {
		sqlDB.SetMaxOpenConns(30)
		sqlDB.SetMaxIdleConns(10)
		return nil
	}
	sqlDB.SetMaxOpenConns(8)
	sqlDB.SetMaxIdleConns(4)
	return nil
}
