package app

import (
	"context"
	"log/slog"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"
)

// h264EncoderEnv 可强制指定 H.264 编码器（如 libx264 / h264_mf / libopenh264）。
const h264EncoderEnv = "CANVAS_H264_ENCODER"

// 候选顺序：libx264（GPL 构建）优先保持原有画质参数；LGPL 构建没有 libx264，
// 依次回退到 OpenH264、Windows MediaFoundation、macOS VideoToolbox。
var h264EncoderCandidates = []string{"libx264", "libopenh264", "h264_mf", "h264_videotoolbox"}

var (
	h264EncoderOnce   sync.Once
	h264EncoderCached string
)

// h264VideoCodecArgs 返回当前 ffmpeg 可用的 H.264 编码参数（含 -c:v）。
func h264VideoCodecArgs() []string {
	return h264ArgsFor(selectedH264Encoder())
}

func h264ArgsFor(encoder string) []string {
	switch encoder {
	case "libx264":
		return []string{"-c:v", "libx264", "-preset", "veryfast", "-crf", "23"}
	default:
		// 其余编码器不支持 crf，用固定码率近似 crf 23 的 1080p 画质。
		return []string{"-c:v", encoder, "-b:v", "6M"}
	}
}

// SelectedH264Encoder 供桌面端 ffmpeg 代理改写前端参数使用。
func SelectedH264Encoder() string { return selectedH264Encoder() }

// FfmpegBinary 返回后端使用的 ffmpeg 可执行文件路径。
func FfmpegBinary() (string, error) { return renderFfmpegBinary() }

func selectedH264Encoder() string {
	h264EncoderOnce.Do(func() {
		h264EncoderCached = detectH264Encoder()
	})
	return h264EncoderCached
}

func detectH264Encoder() string {
	if forced := strings.TrimSpace(os.Getenv(h264EncoderEnv)); forced != "" {
		return forced
	}
	bin, err := renderFfmpegBinary()
	if err != nil {
		// 没有 ffmpeg 时转码本就不会执行，保持默认值。
		return "libx264"
	}
	for _, encoder := range h264EncoderCandidates {
		if h264EncoderWorks(bin, encoder) {
			if encoder != "libx264" {
				slog.Info("libx264 unavailable, using fallback H.264 encoder", "encoder", encoder)
			}
			return encoder
		}
	}
	slog.Warn("no working H.264 encoder found in ffmpeg; transcode will likely fail")
	return "libx264"
}

// h264EncoderWorks 实际编码几帧测试画面：编码器出现在列表里不代表可用
// （如硬件编码器缺驱动）。
func h264EncoderWorks(bin string, encoder string) bool {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	args := append([]string{"-hide_banner", "-loglevel", "error", "-nostdin",
		"-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=0.2",
		"-pix_fmt", "yuv420p"}, h264ArgsFor(encoder)...)
	args = append(args, "-f", "null", "-")
	return exec.CommandContext(ctx, bin, args...).Run() == nil
}
