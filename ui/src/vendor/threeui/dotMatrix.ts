// Adapted from ThreeUI Community's Dot Matrix background (MIT, (c) 2026 Meng To). See README.md and LICENSE here.

export const DOT_MATRIX_VERTEX = `
attribute vec2 aPosition;
void main() {
  gl_Position = vec4(aPosition, 0.0, 1.0);
}
`;

export const DOT_MATRIX_FRAGMENT = `
precision mediump float;
uniform float uTime;
uniform vec2 uResolution;
uniform vec3 uColor;
uniform float uGridScale;
uniform float uPulseSpeed;
uniform float uRadius;
uniform float uOpacity;
void main() {
  vec2 uv = gl_FragCoord.xy / uResolution.xy;
  float aspect = uResolution.x / uResolution.y;
  uv.x *= aspect;
  vec2 grid = fract(uv * uGridScale);
  vec2 id = floor(uv * uGridScale);
  float dist = length(grid - vec2(0.5));
  float pulse = sin(uTime * uPulseSpeed + id.x * 0.05 + id.y * 0.05) * 0.5 + 0.5;
  float radius = 0.08 + pulse * uRadius;
  float alpha = smoothstep(radius, radius - 0.05, dist);
  vec2 center = vec2(0.5 * aspect, 0.5);
  float depthFade = smoothstep(1.2, 0.1, length(uv - center));
  float a = alpha * depthFade * pulse * uOpacity;
  gl_FragColor = vec4(uColor * a, a); // premultiplied, like the canvas
}
`;
