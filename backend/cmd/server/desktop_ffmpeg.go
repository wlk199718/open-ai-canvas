package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"yingce/backend/internal/app"
	"yingce/backend/internal/service"
)

// 桌面端 ffmpeg 代理：替代前端 GPL 授权的 @ffmpeg/core（wasm），改用后端随包的 LGPL ffmpeg。
// 仅在 CANVAS_DESKTOP_FFMPEG=1 时注册；需要登录会话且校验同源 Origin。
//
// 协议：POST /api/desktop/ffmpeg/exec，multipart：
//   - args：JSON 字符串数组（与 ffmpeg.wasm exec 参数一致，最后一个参数为输出文件名）
//   - 其余文件字段：字段名即工作区文件名
// 成功（exit 0）返回输出文件二进制；失败返回 JSON {exitCode, logs}。两种情况都带 X-Ffmpeg-Exit-Code 头。

const (
	desktopFFmpegMaxBody  = 8 << 30 // 8GB：时间线导出可能带多段长视频
	desktopFFmpegMaxArgs  = 256
	desktopFFmpegMaxArg   = 8 << 10
	desktopFFmpegTimeout  = 60 * time.Minute
	desktopFFmpegLogLimit = 64 << 10
)

var (
	desktopFFmpegFileName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)
	// 拒绝 URL/协议（http:、file:、concat: 等）作为参数开头
	desktopFFmpegScheme = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9+.-]*:`)
	desktopFFmpegDrive  = regexp.MustCompile(`(^|[=,'"\s])[A-Za-z]:[\\/]`)
	desktopConcatLine   = regexp.MustCompile(`^file\s+'([^']*)'\s*$`)
	// 可读取任意文件的滤镜
	desktopFFmpegFileFilters = regexp.MustCompile(`(^|[,;\[\]\s])a?movie\s*=`)
)

func registerDesktopFFmpegRoutes(api *gin.RouterGroup, svc *service.Service) {
	api.POST("/desktop/ffmpeg/exec", func(c *gin.Context) {
		if !desktopSameOrigin(c.Request) {
			c.JSON(http.StatusForbidden, gin.H{"code": 403, "msg": "跨源请求被拒绝"})
			return
		}
		cookie, _ := c.Cookie(service.SessionCookieName)
		if user, err := svc.CurrentUser(cookie); err != nil || user == nil {
			c.JSON(http.StatusUnauthorized, gin.H{"code": 401, "msg": "请先登录"})
			return
		}
		runDesktopFFmpeg(c)
	})
}

// desktopSameOrigin 要求 Origin（或 Referer）与请求 Host 一致，防止其他网页借用户会话调用本地 ffmpeg。
func desktopSameOrigin(r *http.Request) bool {
	origin := r.Header.Get("Origin")
	if origin == "" {
		origin = r.Header.Get("Referer")
	}
	if origin == "" {
		return false
	}
	parsed, err := url.Parse(origin)
	return err == nil && parsed.Host != "" && strings.EqualFold(parsed.Host, r.Host)
}

func runDesktopFFmpeg(c *gin.Context) {
	fail := func(status int, msg string) {
		c.JSON(status, gin.H{"code": status, "msg": msg})
	}
	bin, err := app.FfmpegBinary()
	if err != nil {
		fail(http.StatusServiceUnavailable, "未找到 ffmpeg："+err.Error())
		return
	}
	workDir, err := os.MkdirTemp("", "yingce-ffmpeg-")
	if err != nil {
		fail(http.StatusInternalServerError, "创建临时目录失败")
		return
	}
	defer os.RemoveAll(workDir)

	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, desktopFFmpegMaxBody)
	reader, err := c.Request.MultipartReader()
	if err != nil {
		fail(http.StatusBadRequest, "请求格式错误")
		return
	}
	var args []string
	files := map[string]bool{}
	for {
		part, err := reader.NextPart()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			fail(http.StatusBadRequest, "读取上传内容失败")
			return
		}
		name := part.FormName()
		if name == "args" {
			raw, err := io.ReadAll(io.LimitReader(part, 1<<20))
			_ = part.Close()
			if err != nil || json.Unmarshal(raw, &args) != nil {
				fail(http.StatusBadRequest, "args 格式错误")
				return
			}
			continue
		}
		if !desktopFFmpegFileName.MatchString(name) {
			_ = part.Close()
			fail(http.StatusBadRequest, "非法文件名："+name)
			return
		}
		if err := writeDesktopFFmpegFile(filepath.Join(workDir, name), part); err != nil {
			_ = part.Close()
			fail(http.StatusBadRequest, "保存上传文件失败")
			return
		}
		_ = part.Close()
		files[name] = true
	}

	output, err := validateDesktopFFmpegArgs(args, workDir, files)
	if err != nil {
		fail(http.StatusBadRequest, err.Error())
		return
	}
	finalArgs := append([]string{"-hide_banner", "-nostdin", "-y"}, rewriteH264Args(args, app.SelectedH264Encoder())...)

	ctx, cancel := context.WithTimeout(c.Request.Context(), desktopFFmpegTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, finalArgs...)
	cmd.Dir = workDir
	var logs limitedBuffer
	logs.limit = desktopFFmpegLogLimit
	cmd.Stdout = &logs
	cmd.Stderr = &logs
	exitCode := 0
	if err := cmd.Run(); err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			// Windows 下 ffmpeg 的负数 AVERROR 会以 uint32 返回，转回有符号便于排查
			exitCode = int(int32(uint32(exitErr.ExitCode())))
		} else {
			exitCode = -1
		}
	}

	outputPath := filepath.Join(workDir, output)
	if exitCode == 0 {
		if info, err := os.Stat(outputPath); err != nil || info.IsDir() {
			exitCode = 1
		}
	}
	c.Header("X-Ffmpeg-Exit-Code", strconv.Itoa(exitCode))
	if exitCode != 0 {
		c.JSON(http.StatusOK, gin.H{"exitCode": exitCode, "logs": logs.String()})
		return
	}
	c.Header("Content-Type", "application/octet-stream")
	c.File(outputPath)
}

func writeDesktopFFmpegFile(path string, src io.Reader) error {
	dst, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	if _, err := io.Copy(dst, src); err != nil {
		_ = dst.Close()
		return err
	}
	return dst.Close()
}

// validateDesktopFFmpegArgs 限制 ffmpeg 只能读写临时工作区内的文件，返回输出文件名。
func validateDesktopFFmpegArgs(args []string, workDir string, files map[string]bool) (string, error) {
	if len(args) < 2 || len(args) > desktopFFmpegMaxArgs {
		return "", errors.New("args 数量不合法")
	}
	for i, arg := range args {
		if len(arg) > desktopFFmpegMaxArg {
			return "", errors.New("参数过长")
		}
		if strings.Contains(arg, "..") || strings.HasPrefix(arg, "/") || strings.HasPrefix(arg, `\`) ||
			desktopFFmpegScheme.MatchString(arg) || desktopFFmpegDrive.MatchString(arg) || desktopFFmpegFileFilters.MatchString(arg) {
			return "", errors.New("不允许的参数：" + arg)
		}
		if arg == "-i" {
			if i+1 >= len(args) || !files[args[i+1]] {
				return "", errors.New("输入文件未上传")
			}
		}
	}
	output := args[len(args)-1]
	if !desktopFFmpegFileName.MatchString(output) || files[output] {
		return "", errors.New("输出文件名不合法")
	}
	// concat 列表只能引用已上传文件
	for name := range files {
		if !strings.HasSuffix(strings.ToLower(name), ".txt") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(workDir, name))
		if err != nil {
			return "", err
		}
		for _, line := range strings.Split(strings.ReplaceAll(string(raw), "\r\n", "\n"), "\n") {
			line = strings.TrimSpace(line)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			if m := desktopConcatLine.FindStringSubmatch(line); m != nil && !files[m[1]] {
				return "", errors.New("concat 列表引用了未上传的文件")
			}
		}
	}
	return output, nil
}

// rewriteH264Args 把前端写死的 libx264 参数换成当前 ffmpeg 可用的编码器。
func rewriteH264Args(args []string, encoder string) []string {
	if encoder == "" || encoder == "libx264" {
		return args
	}
	out := make([]string, 0, len(args)+2)
	usesX264 := false
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if (arg == "-c:v" || arg == "-vcodec") && i+1 < len(args) && args[i+1] == "libx264" {
			out = append(out, arg, encoder)
			usesX264 = true
			i++
			continue
		}
		out = append(out, arg)
	}
	if !usesX264 {
		return args
	}
	// libx264 专用选项（-preset / -crf / -tune / -profile:v / -x264-params）对回退编码器无效或会报错
	filtered := make([]string, 0, len(out)+2)
	for i := 0; i < len(out); i++ {
		switch out[i] {
		case "-preset", "-crf", "-tune", "-x264-params", "-x264opts":
			i++
			continue
		}
		filtered = append(filtered, out[i])
	}
	// 在输出文件名前补码率，近似 crf 20~23 的画质
	last := len(filtered) - 1
	result := append([]string{}, filtered[:last]...)
	result = append(result, "-b:v", "6M", filtered[last])
	return result
}

type limitedBuffer struct {
	buf   bytes.Buffer
	limit int
}

// Write 只保留最后 limit 字节日志，避免长任务占用大量内存。
func (b *limitedBuffer) Write(p []byte) (int, error) {
	n := len(p)
	b.buf.Write(p)
	if over := b.buf.Len() - b.limit; over > 0 {
		b.buf.Next(over)
	}
	return n, nil
}

func (b *limitedBuffer) String() string { return b.buf.String() }
