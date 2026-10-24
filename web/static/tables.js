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
