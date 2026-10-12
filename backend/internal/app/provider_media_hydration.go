// 生成输入媒体的准备：把画布上的资源引用转换成供应商可读取的形式（签名 URL 或 data URL）。
//
// 供应商能读取公网地址时优先给签名 URL；不能时才内联为 data URL，避免请求体过大。

package app

import (
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"yingce/backend/internal/model"
)

func (s *Service) hydrateGenerationMedia(userID string, input *canvasGenerationInput, policy providerMediaHydrationPolicy) error {
	groups := [][]providerMedia{input.ReferenceImages, input.ReferenceVideos, input.ReferenceAudios}
	for groupIndex, group := range groups {
		mediaPolicy := policy
		// 识图规划与 Agent 看图都发送归属校验后的实际像素，避免供应商无法读取签名 URL。
		// 视频、音频及其他普通生成任务保留原来的协议策略。
		inlineVision := input.Mode == "text" && ((input.AgentRequests != nil && input.AgentRequests.Canonical != nil) || metadataString(input.Metadata, "edit") == "layer-planning")
		if groupIndex == 0 && inlineVision {
			mediaPolicy = providerMediaHydrationPolicy{imageOnly: true}
			if input.Config.CapabilityConfig != nil && input.Config.CapabilityConfig.Text != nil {
				limits := input.Config.CapabilityConfig.Text.References
				if len(group) > limits.MaxImages {
					return errors.New("参考图片数量超过当前模型限制")
				}
				mediaPolicy.maxBytes = limits.MaxImageBytes
			}
		}
		for index := range group {
			if err := s.hydrateProviderMedia(userID, &group[index], mediaPolicy); err != nil {
				return err
			}
		}
	}
	if input.Mask != nil {
		return s.hydrateProviderMedia(userID, input.Mask, policy)
	}
	return nil
}

func (s *Service) hydrateProviderMedia(userID string, media *providerMedia, policy providerMediaHydrationPolicy) error {
	if !strings.HasPrefix(media.StorageKey, "resource:") {
		if policy.requireURL && strings.HasPrefix(strings.TrimSpace(media.DataURL), "data:") {
			return publicMediaURLRequiredError(nil)
		}
		return normalizeInlineMediaType(media)
	}
	resourceID := strings.TrimPrefix(media.StorageKey, "resource:")
	resource, err := s.repo.ResourceForUser(userID, resourceID)
	if err != nil {
		return fmt.Errorf("读取任务参考资源失败：%w", err)
	}
	if resource.Status != "ready" {
		return errors.New("任务参考资源尚未上传完成")
	}
	if policy.imageOnly && !strings.HasPrefix(strings.ToLower(resource.MimeType), "image/") {
		return errors.New("看图资源不是图片")
	}
	if policy.maxBytes > 0 && resource.Size > policy.maxBytes {
		return errors.New("参考图片文件超过当前模型大小限制")
	}
	useObjectURL := policy.requireURL || (policy.preferURL && resourceUsesObjectStorage(resource))
	if useObjectURL {
		signedURL, err := s.providerResourceURL(resource, time.Time{})
		if err != nil {
			if policy.requireURL && publicMediaURLUnavailable(resource, err) {
				return publicMediaURLRequiredError(err)
			}
			return fmt.Errorf("生成参考素材地址失败：%w", err)
		}
		media.URL = signedURL
		media.DataURL = ""
		media.MimeType = firstNonEmpty(media.MimeType, resource.MimeType)
		media.Bytes = resource.Size
		media.Width = resource.Width
		media.Height = resource.Height
		media.DurationMs = resource.DurationMs
		return nil
	}
	// Agent 看图以归属校验后的资源文件为准，不能让附带的内嵌内容替换真实图片。
	if !policy.imageOnly && strings.HasPrefix(strings.TrimSpace(media.DataURL), "data:") {
		return normalizeInlineMediaType(media)
	}
	resource, body, err := s.OpenResource(userID, resourceID)
	if err != nil {
		return fmt.Errorf("读取任务参考资源失败：%w", err)
	}
	defer body.Close()
	runtimePolicy, err := s.RuntimePolicy()
	if err != nil {
		return err
	}
	resourceLimit := megabytes(runtimePolicy.Resource.ResourceUploadMB)
	if policy.maxBytes > 0 {
		resourceLimit = min(resourceLimit, policy.maxBytes)
	}
	data, err := io.ReadAll(io.LimitReader(body, resourceLimit+1))
	if err != nil {
		return err
	}
	if int64(len(data)) > resourceLimit {
		return fmt.Errorf("任务参考资源超过读取上限 %d 字节", resourceLimit)
	}
	if policy.imageOnly && len(data) == 0 {
		return errors.New("看图资源内容为空")
	}
	mimeType := normalizedMediaMimeType(firstNonEmpty(media.MimeType, resource.MimeType), data)
	media.DataURL = dataURL(mimeType, data)
	if policy.imageOnly {
		media.URL = ""
	}
	media.MimeType = mimeType
	media.Bytes = int64(len(data))
	media.Width = resource.Width
	media.Height = resource.Height
	media.DurationMs = resource.DurationMs
	return nil
}

func normalizeInlineMediaType(media *providerMedia) error {
	if !strings.HasPrefix(strings.TrimSpace(media.DataURL), "data:image/") {
		return nil
	}
	declared, data, err := decodeProviderDataURL(strings.TrimSpace(media.DataURL))
	if err != nil {
		return err
	}
	mimeType := normalizedMediaMimeType(declared, data)
	media.DataURL = dataURL(mimeType, data)
	media.MimeType = mimeType
	return nil
}

// ReasonPublicMediaURLRequired：当前模型协议只接受公网素材地址，而参考素材无法生成模型服务可访问的地址
// （常见于桌面版或未配置对象存储的本地存储部署）。前端按该 reason / 文案给出配置指引。
const ReasonPublicMediaURLRequired ErrorReason = "public_media_url_required"

const publicMediaURLRequiredMessage = "当前视频模型需要公网可访问的参考素材地址，本地存储的素材无法被模型服务读取。" +
	"请在「设置 → 我的对象存储」启用阿里云 OSS、腾讯云 COS、七牛云或 S3 兼容存储后，重新上传参考素材再生成；" +
	"或改用支持直接上传素材的模型。"

func publicMediaURLRequiredError(cause error) *AppError {
	err := NewAppError(http.StatusBadRequest, publicMediaURLRequiredMessage)
	err.Reason = ReasonPublicMediaURLRequired
	err.Cause = cause
	return err
}

// publicMediaURLUnavailable 判断签发失败是否属于「没有可公网访问的地址」，其他错误（签名异常、过期等）保持原样上报。
func publicMediaURLUnavailable(resource *model.Resource, err error) bool {
	var appErr *AppError
	if !errors.As(err, &appErr) {
		return false
	}
	if !resourceUsesObjectStorage(resource) {
		// 本地存储只有在配置了服务器访问地址时才能对外提供素材；其他错误（磁盘、签名等）保留原因
		return appErr.Reason == reasonPublicBaseURLUnconfigured
	}
	return appErr.Reason == "resource_origin_private"
}

// reasonPublicBaseURLUnconfigured 标记本地存储缺少服务器访问地址（CANVAS_PUBLIC_BASE_URL）。
const reasonPublicBaseURLUnconfigured ErrorReason = "public_base_url_unconfigured"

func resourceUsesObjectStorage(resource *model.Resource) bool {
	if resource == nil {
		return false
	}
	provider := strings.ToLower(strings.TrimSpace(resource.Provider))
	return provider != "" && provider != "local"
}

func normalizedMediaMimeType(declared string, data []byte) string {
	declared = strings.TrimSpace(strings.Split(declared, ";")[0])
	detected := strings.TrimSpace(strings.Split(http.DetectContentType(data), ";")[0])
	// 上游响应头和历史资源可能误标图片格式，内联头必须与实际字节一致。
	switch detected {
	case "image/png", "image/jpeg", "image/webp", "image/gif":
		return detected
	}
	if declared != "" && declared != "application/octet-stream" {
		return declared
	}
	return defaultString(detected, "application/octet-stream")
}
