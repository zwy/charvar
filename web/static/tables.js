/* 共享表（预设页 / 图片页 / 视频页都用同一份，改一处要同步另一处）。

   即梦画布（dreamina-canvas）模型目录：与 charvar/dreamina_canvas.py 的
   _IMAGE_MODELS / _VIDEO_MODELS 手工同源（CLI 1.3.0、2026-11-11 实测）。
   resolution/ratio 字面值大小写按目录原样（seedance 小写 720p、happyhorse/
   wan 大写 720P），提交时透传 —— 别自己归一大写。CLI 升级后要同步两张表
   （命令是 dreamina-canvas model list --type image / video）。 */
const DREAMINA_IMAGE_MODELS = {
  "seedream_4.0":       { label: "Seedream 4.0",        promptLimit: 2000, resolutions: ["2K", "4K"],        ratios: ["1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"], refMax: { t2i: 0,  i2i: 6 } },
  "seedream_4.1":       { label: "Seedream 4.1",        promptLimit: 2000, resolutions: ["2K", "4K"],        ratios: ["1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"], refMax: { t2i: 0,  i2i: 6 } },
  "seedream_4.5":       { label: "Seedream 4.5",        promptLimit: 2000, resolutions: ["2K", "4K"],        ratios: ["1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"], refMax: { t2i: 0,  i2i: 6 } },
  "seedream_4.6":       { label: "Seedream 4.6",        promptLimit: 2000, resolutions: ["2K", "4K"],        ratios: ["1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"], refMax: { t2i: 14, i2i: 6 } },
  "seedream_4.7":       { label: "Seedream 4.7",        promptLimit: 2000, resolutions: ["1K", "2K", "4K"],  ratios: ["1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"], refMax: { t2i: 14, i2i: 6 } },
  "seedream_5.0_lite":  { label: "Seedream 5.0 Lite",   promptLimit: 2000, resolutions: ["2K", "4K"],        ratios: ["1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"], refMax: { t2i: 0,  i2i: 6 } },
  "seedream_5.0_pro":   { label: "Seedream 5.0 Pro",    promptLimit: 5000, resolutions: ["1.5K", "2K", "4K"], ratios: ["1:1", "9:16", "16:9", "3:4", "4:3", "2:3", "3:2", "9:21", "21:9"], refMax: { t2i: 14, i2i: 10 } },
  "seedream_5.5":       { label: "Seedream 5.5",        promptLimit: 5000, resolutions: ["1.5K", "2K"],      ratios: ["1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"], refMax: { t2i: 14, i2i: 10 } },
  "high_aes_general_v50_flash": { label: "智能图片V2.5 Pro", promptLimit: 5000, resolutions: ["1.5K", "2K"], ratios: ["1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"], refMax: { t2i: 0, i2i: 10 } },
  "jimeng_smart_image_v1": { label: "即梦 ImageMax 1.0", promptLimit: 5000, resolutions: ["1K", "2K", "4K"], ratios: ["1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"], refMax: { t2i: 0, i2i: 4 } },
  "jm_image_model_yc_mj82": { label: "图片美学模型 V8.2", promptLimit: 1024, resolutions: ["1K", "2K"], ratios: ["1:1", "1:2", "2:1", "9:16", "16:9", "3:4", "4:3", "2:3", "3:2", "4:5", "5:4", "9:21", "21:9", "1:3", "3:1"], refMax: { t2i: 0, i2i: 20 } },
};

/* 即梦视频：seedance 系 / happyhorse / wan；modes = ["t2v","m2v","first_last_frame"]；
   m2vRef = 全能参考上限 { image, video, total }（happyhorse 不收参考视频）。
   样片模式 seedance_2.5_draft 与纯首尾帧 seedance_pro_fast 见表内例外。 */
const DREAMINA_VIDEO_MODELS = {
  "seedance_2.0_fast_vip": { label: "即梦 Seedance 2.0 Fast VIP", resolutions: ["720p"],                     durations: [4, 15], ratios: ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], modes: ["t2v", "m2v", "first_last_frame"], m2vRef: { image: 9, video: 3, total: 12 } },
  "seedance_2.0_mini":     { label: "即梦 Seedance 2.0 mini",     resolutions: ["720p"],                     durations: [4, 15], ratios: ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], modes: ["t2v", "m2v", "first_last_frame"], m2vRef: { image: 9, video: 3, total: 12 } },
  "seedance_2.0_vip":      { label: "即梦 Seedance 2.0 VIP",      resolutions: ["720p", "1080p", "4k"],      durations: [4, 15], ratios: ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], modes: ["t2v", "m2v", "first_last_frame"], m2vRef: { image: 9, video: 3, total: 12 } },
  "seedance_2.5":          { label: "即梦 Seedance 2.5",          resolutions: ["480p", "720p", "1080p"],    durations: [4, 30], ratios: ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], modes: ["t2v", "m2v", "first_last_frame"], m2vRef: { image: 30, video: 10, total: 50 } },
  "happyhorse_1.1":        { label: "HappyHorse 1.1",            resolutions: ["720P", "1080P"],            durations: [4, 15], ratios: ["16:9", "9:16", "1:1", "4:3", "3:4", "4:5", "5:4", "9:21", "21:9"], modes: ["t2v", "m2v", "first_last_frame"], m2vRef: { image: 9, video: 0, total: 9 } },
  "wan_3.0":               { label: "Wan 3.0",                   resolutions: ["720P", "1080P"],            durations: [4, 15], ratios: ["16:9", "4:3", "1:1", "3:4", "9:16"], modes: ["t2v", "m2v", "first_last_frame"], m2vRef: { image: 10, video: 5, total: 20 } },
  "seedance_pro_fast":     { label: "即梦 Seedance 1.0 Fast",    resolutions: ["720p", "1080p"],            durations: [5, 10], ratios: ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], modes: ["t2v", "first_last_frame"], m2vRef: { image: 0, video: 0, total: 0 } },
};

/* 即梦默认档（与 charvar/dreamina_canvas.py 的默认一致） */
const DREAMINA_DEFAULT_IMAGE_MODEL = "seedream_4.7";
const DREAMINA_DEFAULT_VIDEO_MODEL = "seedance_2.0_fast_vip";

/* 视频画幅的通用兜底集（模型各自的 ratios 在 DREAMINA_VIDEO_MODELS 里；
   缺档位上下文时用这套）。别复用生图侧的表 —— 生图有 2:3/3:2。 */
const DREAMINA_VIDEO_RATIOS = ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"];
