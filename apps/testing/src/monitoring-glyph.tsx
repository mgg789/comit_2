const base = import.meta.env.BASE_URL;
type Fragment = { file: string; x: number; y: number; width: number; height: number };
// Exact positioned Figma vector exports; stroke expansion shifts each origin by 1.5px.
const face: Fragment[] = [
  {file:'fullscreen-corner.svg',x:4.333,y:4.333,width:8.833,height:8.833},
  {file:'fullscreen-corner-2.svg',x:4.333,y:21.833,width:8.833,height:8.833},
  {file:'fullscreen-corner-3.svg',x:21.833,y:4.333,width:8.833,height:8.833},
  {file:'fullscreen-corner-4.svg',x:21.833,y:21.833,width:8.833,height:8.833},
  {file:'fullscreen-divider.svg',x:11.625,y:13.083,width:3.014,height:3},
  {file:'fullscreen-divider.svg',x:20.375,y:13.083,width:3.014,height:3},
  {file:'fullscreen-center.svg',x:12.354,y:20.375,width:10.292,height:4.532},
];
const screen: Fragment[] = [
  {file:'camera-body.svg',x:2.875,y:5.79,width:29.25,height:23.417},
  {file:'camera-details.svg',x:7.25,y:10.17,width:3.015,height:3},
  {file:'camera-details.svg',x:11.625,y:10.17,width:3.015,height:3},
];
/** Original Figma fragments compose each monitoring glyph and inherit its status color. */
export function MonitoringGlyph({kind}: {kind:'face'|'screen'}) {
  return <span aria-hidden="true">{(kind === 'face' ? face : screen).map((part,index) => <span key={index} className="fragment" style={{left:`${part.x}rem`,top:`${part.y}rem`,width:`${part.width}rem`,height:`${part.height}rem`,maskImage:`url("${base}design/${part.file}")`}} />)}</span>;
}
