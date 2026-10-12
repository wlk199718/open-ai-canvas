# 本地生成接口

配置字段 `apiKey` 为生成连接令牌，不能使用管理会话令牌。

创建 `POST /api/v1/tasks`，轮询 `GET /api/v1/tasks/{taskId}`，
结果下载 `GET /api/v1/tasks/{taskId}/result`，三个阶段都使用 Bearer 鉴权。
创建携带宿主幂等键，不重复提交计费任务。

响应中的 `id`、`status`、`error` 分别映射任务 ID、状态和错误。
`completed` 后由宿主鉴权下载图片或视频，再保存到本机素材库。
`poll_failed`、`download_failed`、`submission_unknown` 按失败上报，可在服务管理页处理。

图片与 Q4 接受内联 PNG/JPEG/WebP 参考图，由中间服务落盘再上传。
Wan 仍要求公开 HTTPS 参考素材，需在造物设置中配置对象存储；Claw 不接受参考图。
当前每个任务只交付一个结果，模型配置限制输出数量为 1。
模型、比例、分辨率和视频时长来自本地服务模型目录。

<!-- YINGCE_MANIFEST_CONTRACT_START -->
## Manifest 完整接口定义

以下 JSON 与插件包内实际 `manifest.json` 逐字段一致，覆盖插件身份、权限、配置、鉴权、参数、校验、创建、Agent、查询、取消、结果下载、响应和 Agent 响应映射。`documentation` 字段的值就是当前完整文档；为避免文档在自身内部无限递归，JSON 中仅用等义占位文本表示正文。

```json
{
  "apiVersion": "yingce.plugin/v2",
  "id": "zaowu-local",
  "name": "造物本地生成",
  "version": "1.0.0",
  "author": "造物",
  "description": "连接本机图片、Wan、Q4 和 Claw 生成服务。",
  "permissions": [
    "generation.run",
    "media.read"
  ],
  "configuration": {
    "fields": [
      {
        "name": "apiKey",
        "type": "secret",
        "label": "连接令牌",
        "required": true
      }
    ]
  },
  "contributes": {
    "providers": [
      {
        "id": "zaowu-local-image",
        "label": "本地图片",
        "capabilities": [
          "image"
        ],
        "scopes": [
          "user.custom-channel",
          "canvas",
          "creation",
          "agent"
        ],
        "baseUrl": "http://127.0.0.1:8787",
        "requiresPublicMediaUrls": false,
        "auth": {
          "type": "bearer",
          "field": "apiKey"
        },
        "create": {
          "method": "POST",
          "path": "/api/v1/tasks",
          "originPath": true,
          "contentType": "application/json",
          "headers": {
            "Idempotency-Key": {
              "$ref": "request.extra.idempotencyKey"
            }
          },
          "body": {
            "provider": "image",
            "task_type": "image",
            "model": {
              "$ref": "request.model"
            },
            "prompt": {
              "$ref": "request.prompt"
            },
            "aspect_ratio": {
              "$coalesce": [
                {
                  "$ref": "request.aspectRatio"
                },
                "1:1"
              ]
            },
            "resolution": {
              "$coalesce": [
                {
                  "$ref": "request.quality"
                },
                "1080p"
              ]
            },
            "sample_count": 1,
            "references": {
              "$map": {
                "from": {
                  "$sortByOrder": {
                    "$ref": "request.images"
                  }
                },
                "as": "media",
                "in": {
                  "$coalesce": [
                    {
                      "$ref": "media.dataUrl"
                    },
                    {
                      "$ref": "media.url"
                    }
                  ]
                }
              }
            }
          }
        },
        "poll": {
          "method": "GET",
          "path": "/api/v1/tasks/{{taskId}}",
          "originPath": true
        },
        "result": {
          "method": "GET",
          "path": "/api/v1/tasks/{{taskId}}/result",
          "originPath": true
        },
        "response": {
          "taskIdPaths": [
            "id"
          ],
          "statusPaths": [
            "status"
          ],
          "messagePaths": [
            "error"
          ],
          "resultKind": "image",
          "status": {
            "$switch": {
              "cases": [
                {
                  "when": {
                    "$in": [
                      {
                        "$ref": "response.status"
                      },
                      [
                        "poll_failed",
                        "download_failed",
                        "submission_unknown"
                      ]
                    ]
                  },
                  "then": "failed"
                }
              ],
              "default": {
                "$ref": "response.status"
              }
            }
          }
        }
      },
      {
        "id": "zaowu-local-q4",
        "label": "本地 Q4 视频",
        "capabilities": [
          "video"
        ],
        "scopes": [
          "user.custom-channel",
          "canvas",
          "creation",
          "agent"
        ],
        "baseUrl": "http://127.0.0.1:8787",
        "requiresPublicMediaUrls": false,
        "auth": {
          "type": "bearer",
          "field": "apiKey"
        },
        "create": {
          "method": "POST",
          "path": "/api/v1/tasks",
          "originPath": true,
          "contentType": "application/json",
          "headers": {
            "Idempotency-Key": {
              "$ref": "request.extra.idempotencyKey"
            }
          },
          "body": {
            "provider": "q4-video",
            "task_type": "video",
            "model": {
              "$ref": "request.model"
            },
            "prompt": {
              "$ref": "request.prompt"
            },
            "duration": {
              "$ref": "request.duration"
            },
            "aspect_ratio": {
              "$coalesce": [
                {
                  "$ref": "request.aspectRatio"
                },
                "16:9"
              ]
            },
            "resolution": {
              "$coalesce": [
                {
                  "$ref": "request.resolution"
                },
                "720p"
              ]
            },
            "sample_count": 1,
            "references": {
              "$map": {
                "from": {
                  "$sortByOrder": {
                    "$ref": "request.images"
                  }
                },
                "as": "media",
                "in": {
                  "$coalesce": [
                    {
                      "$ref": "media.dataUrl"
                    },
                    {
                      "$ref": "media.url"
                    }
                  ]
                }
              }
            }
          }
        },
        "poll": {
          "method": "GET",
          "path": "/api/v1/tasks/{{taskId}}",
          "originPath": true
        },
        "result": {
          "method": "GET",
          "path": "/api/v1/tasks/{{taskId}}/result",
          "originPath": true
        },
        "response": {
          "taskIdPaths": [
            "id"
          ],
          "statusPaths": [
            "status"
          ],
          "messagePaths": [
            "error"
          ],
          "resultKind": "video",
          "status": {
            "$switch": {
              "cases": [
                {
                  "when": {
                    "$in": [
                      {
                        "$ref": "response.status"
                      },
                      [
                        "poll_failed",
                        "download_failed",
                        "submission_unknown"
                      ]
                    ]
                  },
                  "then": "failed"
                }
              ],
              "default": {
                "$ref": "response.status"
              }
            }
          }
        }
      },
      {
        "id": "zaowu-local-claw",
        "label": "本地 Claw 视频",
        "capabilities": [
          "video"
        ],
        "scopes": [
          "user.custom-channel",
          "canvas",
          "creation",
          "agent"
        ],
        "baseUrl": "http://127.0.0.1:8787",
        "requiresPublicMediaUrls": false,
        "auth": {
          "type": "bearer",
          "field": "apiKey"
        },
        "create": {
          "method": "POST",
          "path": "/api/v1/tasks",
          "originPath": true,
          "contentType": "application/json",
          "headers": {
            "Idempotency-Key": {
              "$ref": "request.extra.idempotencyKey"
            }
          },
          "body": {
            "provider": "claw-video",
            "task_type": "video",
            "model": {
              "$ref": "request.model"
            },
            "prompt": {
              "$ref": "request.prompt"
            },
            "duration": {
              "$ref": "request.duration"
            },
            "aspect_ratio": {
              "$coalesce": [
                {
                  "$ref": "request.aspectRatio"
                },
                "16:9"
              ]
            },
            "resolution": {
              "$coalesce": [
                {
                  "$ref": "request.resolution"
                },
                "720p"
              ]
            },
            "sample_count": 1,
            "references": []
          }
        },
        "poll": {
          "method": "GET",
          "path": "/api/v1/tasks/{{taskId}}",
          "originPath": true
        },
        "result": {
          "method": "GET",
          "path": "/api/v1/tasks/{{taskId}}/result",
          "originPath": true
        },
        "response": {
          "taskIdPaths": [
            "id"
          ],
          "statusPaths": [
            "status"
          ],
          "messagePaths": [
            "error"
          ],
          "resultKind": "video",
          "status": {
            "$switch": {
              "cases": [
                {
                  "when": {
                    "$in": [
                      {
                        "$ref": "response.status"
                      },
                      [
                        "poll_failed",
                        "download_failed",
                        "submission_unknown"
                      ]
                    ]
                  },
                  "then": "failed"
                }
              ],
              "default": {
                "$ref": "response.status"
              }
            }
          }
        }
      },
      {
        "id": "zaowu-local-wan",
        "label": "本地 Wan 视频",
        "capabilities": [
          "video"
        ],
        "scopes": [
          "user.custom-channel",
          "canvas",
          "creation",
          "agent"
        ],
        "baseUrl": "http://127.0.0.1:8787",
        "requiresPublicMediaUrls": true,
        "auth": {
          "type": "bearer",
          "field": "apiKey"
        },
        "create": {
          "method": "POST",
          "path": "/api/v1/tasks",
          "originPath": true,
          "contentType": "application/json",
          "headers": {
            "Idempotency-Key": {
              "$ref": "request.extra.idempotencyKey"
            }
          },
          "body": {
            "provider": "wan-video",
            "task_type": "video",
            "model": {
              "$ref": "request.model"
            },
            "prompt": {
              "$ref": "request.prompt"
            },
            "duration": {
              "$ref": "request.duration"
            },
            "aspect_ratio": {
              "$coalesce": [
                {
                  "$ref": "request.aspectRatio"
                },
                "16:9"
              ]
            },
            "resolution": "1080p",
            "sample_count": 1,
            "references": {
              "$map": {
                "from": {
                  "$sortByOrder": {
                    "$ref": "request.images"
                  }
                },
                "as": "media",
                "in": {
                  "$ref": "media.url"
                }
              }
            }
          }
        },
        "poll": {
          "method": "GET",
          "path": "/api/v1/tasks/{{taskId}}",
          "originPath": true
        },
        "result": {
          "method": "GET",
          "path": "/api/v1/tasks/{{taskId}}/result",
          "originPath": true
        },
        "response": {
          "taskIdPaths": [
            "id"
          ],
          "statusPaths": [
            "status"
          ],
          "messagePaths": [
            "error"
          ],
          "resultKind": "video",
          "status": {
            "$switch": {
              "cases": [
                {
                  "when": {
                    "$in": [
                      {
                        "$ref": "response.status"
                      },
                      [
                        "poll_failed",
                        "download_failed",
                        "submission_unknown"
                      ]
                    ]
                  },
                  "then": "failed"
                }
              ],
              "default": {
                "$ref": "response.status"
              }
            }
          }
        }
      }
    ]
  },
  "documentation": "<当前插件的完整 documentation，由 README.md 与 docs/interface.md 拼接而成；为避免 JSON 递归，此处不重复展开正文。>"
}
```
<!-- YINGCE_MANIFEST_CONTRACT_END -->
