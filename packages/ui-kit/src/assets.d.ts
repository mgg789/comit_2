/** Bundler-resolved static assets: Vite turns these imports into hashed URLs in every app. */
declare module "*.svg" {
  const url: string;
  export default url;
}
declare module "*.png" {
  const url: string;
  export default url;
}
