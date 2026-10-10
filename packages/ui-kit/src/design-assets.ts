/** Original exported artwork bundled by Vite for either application. */
const artwork = {
  "public-login.svg": new URL("./design-assets/public-login.svg", import.meta.url).href,
  "6d4cf8ae48179dc57156f25db4b058cc5cb57a8e.svg": new URL("./design-assets/6d4cf8ae48179dc57156f25db4b058cc5cb57a8e.svg", import.meta.url).href,
  "268c55656abd4564896f3ed9b7ea042f24cbf9d6.svg": new URL("./design-assets/268c55656abd4564896f3ed9b7ea042f24cbf9d6.svg", import.meta.url).href,
  "43ffb38ed0f5604cad3f1adb9680d6c0b20d69c9.svg": new URL("./design-assets/43ffb38ed0f5604cad3f1adb9680d6c0b20d69c9.svg", import.meta.url).href,
  "2cb013b9ea483d6e784177684fd4c6fbb33f9969.svg": new URL("./design-assets/2cb013b9ea483d6e784177684fd4c6fbb33f9969.svg", import.meta.url).href,
  "c56e9cc719a6147a0c25e9bc7877291a90ef720e.svg": new URL("./design-assets/c56e9cc719a6147a0c25e9bc7877291a90ef720e.svg", import.meta.url).href,
  "f82aa184ea85eb5960407a62897d24d7084bfd33.svg": new URL("./design-assets/f82aa184ea85eb5960407a62897d24d7084bfd33.svg", import.meta.url).href,
  "944a3f714471682b2ae2b66d8711c46598ca1e19.svg": new URL("./design-assets/944a3f714471682b2ae2b66d8711c46598ca1e19.svg", import.meta.url).href,
  "6da721179169b7fd86cc9afd7a125e469ad945bd.svg": new URL("./design-assets/6da721179169b7fd86cc9afd7a125e469ad945bd.svg", import.meta.url).href,
  "be7a21bfb4b868880613999d5d0bc4a2650413ed.svg": new URL("./design-assets/be7a21bfb4b868880613999d5d0bc4a2650413ed.svg", import.meta.url).href,
  "vacancy-card-chevron.svg": new URL("./design-assets/vacancy-card-chevron.svg", import.meta.url).href,
  "vacancy-edit-pencil.svg": new URL("./design-assets/vacancy-edit-pencil.svg", import.meta.url).href,
  "29795c078097fdcbdd8d396900a5dfd4a5309de8.svg": new URL("./design-assets/29795c078097fdcbdd8d396900a5dfd4a5309de8.svg", import.meta.url).href,
  "message-circle.svg": new URL("./design-assets/message-circle.svg", import.meta.url).href,
  "dec04dda54551634f35985932f0a53e8bcda20f5.svg": new URL("./design-assets/dec04dda54551634f35985932f0a53e8bcda20f5.svg", import.meta.url).href,
  "8af299778072944cf9ce5dadca62d1ec4137e491.svg": new URL("./design-assets/8af299778072944cf9ce5dadca62d1ec4137e491.svg", import.meta.url).href,
  "6e1dc9326ee3cba6de25bb4bcafc3823eaaaee71.svg": new URL("./design-assets/6e1dc9326ee3cba6de25bb4bcafc3823eaaaee71.svg", import.meta.url).href,
  "b2e5fa1dc7bd97f4194c3281ddf16908ee7734b3.png": new URL("./design-assets/b2e5fa1dc7bd97f4194c3281ddf16908ee7734b3.png", import.meta.url).href,
  "13441c23b2e09a38d4fef98d24cccd9bf355d639.png": new URL("./design-assets/13441c23b2e09a38d4fef98d24cccd9bf355d639.png", import.meta.url).href,
  "9ac3e2c307951c9256502142bc9fbad65b066af3.svg": new URL("./design-assets/9ac3e2c307951c9256502142bc9fbad65b066af3.svg", import.meta.url).href,
  "24395aac95170e79b172ce4e75051e1ad4faeed7.svg": new URL("./design-assets/24395aac95170e79b172ce4e75051e1ad4faeed7.svg", import.meta.url).href,
  "c179aee8cef2e1b481a0ab48e2c6a5af19c286b9.svg": new URL("./design-assets/c179aee8cef2e1b481a0ab48e2c6a5af19c286b9.svg", import.meta.url).href,
  "4ecbfcd7e7f3629f251bb7b396bfec64aecee739.svg": new URL("./design-assets/4ecbfcd7e7f3629f251bb7b396bfec64aecee739.svg", import.meta.url).href,
  "a3c8e3c0abb4dfca0ad94e35ca485cbec998042f.svg": new URL("./design-assets/a3c8e3c0abb4dfca0ad94e35ca485cbec998042f.svg", import.meta.url).href,
  "2912dfd1b1af75565d6fd5f86cbe23776dc7799f.png": new URL("./design-assets/2912dfd1b1af75565d6fd5f86cbe23776dc7799f.png", import.meta.url).href,
  "6f0a38215d44082a8500e6aa08c4a16a70316dc5.png": new URL("./design-assets/6f0a38215d44082a8500e6aa08c4a16a70316dc5.png", import.meta.url).href,
  "otp-glass-effect-2.svg": new URL("./design-assets/otp-glass-effect-2.svg", import.meta.url).href,
  "otp-extra-vector.svg": new URL("./design-assets/otp-extra-vector.svg", import.meta.url).href,
  "clear-conversation.svg": new URL("./design-assets/clear-conversation.svg", import.meta.url).href,
  "usage-stats.svg": new URL("./design-assets/usage-stats.svg", import.meta.url).href,
  "reasoning.svg": new URL("./design-assets/reasoning.svg", import.meta.url).href,
  "attached-file.svg": new URL("./design-assets/attached-file.svg", import.meta.url).href,
  "chevron-wide.svg": new URL("./design-assets/chevron-wide.svg", import.meta.url).href,
  "radar-remove.svg": new URL("./design-assets/radar-remove.svg", import.meta.url).href,
} as const;

/** Name of a bundled design asset. */
export type DesignAssetName = keyof typeof artwork;

/** Resolve shared artwork without depending on another application deployment. */
export function designAsset(name: DesignAssetName): string {
  return artwork[name];
}
