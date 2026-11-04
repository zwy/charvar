/* 共享表（预设页 / Playground 页 / 视频页都用同一份，改一处要同步另一处）。

   即梦 model_version → 分辨率枚举（对齐 charvar/dreamina_image._MODEL_RESOLUTIONS，
   CLI 1.4.20）。模型版本下拉与分辨率选项按这张表配对，CLI 升级后要同步。 */
const DREAMINA_MODEL_RESOLUTIONS = {
  "3.0": ["1k", "2k"],
  "3.1": ["1k", "2k"],
  "4.0": ["2k", "4k"],
  "4.1": ["2k", "4k"],
  "4.5": ["2k", "4k"],
  "4.6": ["2k", "4k"],
  "4.7": ["2k", "4k"],
  "5.0": ["2k", "4k"],
  "5.0Pro": ["1.5k", "2k", "4k"],
};

/* 仅文生图的模型版本（3.0/3.1 没有 image2image）：带参考图时不能选。
   预设页与 Playground 页共用 —— 谁先加载谁都能用（别放单个页面文件里）。 */
const DREAMINA_T2I_ONLY_MODELS = new Set(["3.0", "3.1"]);

/* 即梦视频（Dreamina video）枚举：对齐 charvar/dreamina_video._VIDEO_MODELS，
   CLI 1.4.21。model_version → { resolutions, durations:[下限,上限], modes:[t2v/i2v] }。
   视频画幅一律 6 档（DREAMINA_VIDEO_RATIOS）—— 没有生图侧的 2:3/3:2，别复用上面的表。
   seedance2.0fast_vip = 默认（恒 720p / 4-15s）；2.5 是 VIP-only（账号 standard 不可用）。
   CLI 升级后要同步（与 python 侧 _VIDEO_MODELS 两处同源）。 */
const DREAMINA_VIDEO_RATIOS = ["1:1", "3:4", "16:9", "4:3", "9:16", "21:9"];
const DREAMINA_VIDEO_MODELS = {
  "seedance2.0":         { resolutions: ["720p"], durations: [4, 15], modes: ["t2v", "i2v"] },
  "seedance2.0fast":     { resolutions: ["720p"], durations: [4, 15], modes: ["t2v", "i2v"] },
  "seedance2.0_vip":     { resolutions: ["720p", "1080p", "4k"], durations: [4, 15], modes: ["t2v", "i2v"] },
  "seedance2.0fast_vip": { resolutions: ["720p"], durations: [4, 15], modes: ["t2v", "i2v"] },
  "seedance2.0mini":     { resolutions: ["720p"], durations: [4, 15], modes: ["t2v", "i2v"] },
  "seedance2.5":         { resolutions: ["480p", "720p", "1080p"], durations: [4, 30], modes: ["t2v", "i2v"], vipOnly: true, i2vRatioLocked: true },
  "seedance1.0fast":     { resolutions: ["720p"], durations: [5, 10], modes: ["i2v"] },
  "seedance1.5pro":      { resolutions: ["720p"], durations: [5, 12], modes: ["i2v"] },
};
