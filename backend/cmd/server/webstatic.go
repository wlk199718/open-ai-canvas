package main

import (
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strings"

	"github.com/gin-gonic/gin"
)

// webStaticHandler 在桌面端替代 nginx：托管 web/dist，SPA 路由回退到 index.html。
// 仅在设置 CANVAS_WEB_DIR 时启用；/api 与 /oauth 请求交给 fallback（原 NoRoute 逻辑）。
func webStaticHandler(webDir string, fallback gin.HandlerFunc) gin.HandlerFunc {
	root, err := filepath.Abs(webDir)
	if err != nil {
		root = webDir
	}
	indexPath := filepath.Join(root, "index.html")
	return func(c *gin.Context) {
		reqPath := c.Request.URL.Path
		if strings.HasPrefix(reqPath, "/api/") || strings.HasPrefix(reqPath, "/oauth/") {
			fallback(c)
			return
		}
		if c.Request.Method != http.MethodGet && c.Request.Method != http.MethodHead {
			c.AbortWithStatus(http.StatusNotFound)
			return
		}
		c.Header("Referrer-Policy", "no-referrer")
		clean := path.Clean("/" + reqPath)
		// 与 nginx 规则一致：隐藏 .canvas- 内部文件。
		if strings.Contains(clean, "/.canvas-") {
			c.AbortWithStatus(http.StatusNotFound)
			return
		}
		if clean != "/" {
			// path.Clean 已消除 ".."，再用 Rel 校验确保不越出 root。
			full := filepath.Join(root, filepath.FromSlash(strings.TrimPrefix(clean, "/")))
			if rel, err := filepath.Rel(root, full); err == nil && !strings.HasPrefix(rel, "..") {
				if info, err := os.Stat(full); err == nil && !info.IsDir() {
					if strings.HasPrefix(clean, "/assets/") {
						c.Header("Cache-Control", "public, max-age=31536000, immutable")
					} else {
						c.Header("Cache-Control", "no-cache, must-revalidate")
					}
					c.File(full)
					return
				}
			}
			// 带扩展名的 /assets/ 资源缺失时返回 404，避免把 HTML 当 JS 执行。
			if strings.HasPrefix(clean, "/assets/") && path.Ext(clean) != "" {
				c.Header("Cache-Control", "no-store")
				c.AbortWithStatus(http.StatusNotFound)
				return
			}
		}
		c.Header("Cache-Control", "no-cache, must-revalidate")
		c.File(indexPath)
	}
}
