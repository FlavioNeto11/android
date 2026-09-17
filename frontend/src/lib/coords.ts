/**
 * Conversão de coordenadas da tela (CSS px dentro da caixa que exibe o screenshot) para pixels do aparelho.
 *
 * A imagem é exibida com `object-fit: contain`: ela é escalada uniformemente e centralizada, sobrando
 * margens (pillarbox à esquerda/direita ou letterbox em cima/baixo). O tamanho "real" do aparelho vem
 * SEMPRE de `FrameInfo.width/height` — o JPEG pode ter sido reduzido, então `naturalWidth/Height` da
 * imagem nunca entra na conta (a proporção é a mesma, só a resolução muda).
 */

export interface Size {
  width: number;
  height: number;
}

export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface DevicePoint {
  x: number;
  y: number;
}

export interface MapOptions {
  /**
   * `false` (padrão): pontos fora da imagem (nas margens) são rejeitados → `null`.
   * `true`: o ponto é trazido para a borda mais próxima da imagem (útil para o fim de um arrasto que
   * "escapou" da tela).
   */
  clampOutside?: boolean;
}

function isPositive(n: number): boolean {
  return Number.isFinite(n) && n > 0;
}

/** Retângulo (em CSS px, relativo à caixa) efetivamente ocupado pela imagem com `object-fit: contain`. */
export function containedRect(box: Size, frame: Size): Rect | null {
  if (!isPositive(box.width) || !isPositive(box.height) || !isPositive(frame.width) || !isPositive(frame.height)) {
    return null;
  }
  const scale = Math.min(box.width / frame.width, box.height / frame.height);
  const width = frame.width * scale;
  const height = frame.height * scale;
  return { left: (box.width - width) / 2, top: (box.height - height) / 2, width, height };
}

/**
 * @param px    posição X do ponteiro relativa ao canto superior esquerdo da caixa (CSS px)
 * @param py    posição Y do ponteiro relativa ao canto superior esquerdo da caixa (CSS px)
 * @param box   tamanho da caixa que contém a imagem (CSS px)
 * @param frame tamanho do aparelho em pixels (`FrameInfo.width/height`)
 * @returns coordenadas inteiras em pixels do aparelho, limitadas a [0, width-1] × [0, height-1];
 *          `null` se o ponto caiu na margem (e `clampOutside` não foi pedido) ou se os tamanhos são inválidos.
 */
export function mapPointToDevice(
  px: number,
  py: number,
  box: Size,
  frame: Size,
  options: MapOptions = {},
): DevicePoint | null {
  if (!Number.isFinite(px) || !Number.isFinite(py)) return null;
  const rect = containedRect(box, frame);
  if (!rect) return null;

  const relX = px - rect.left;
  const relY = py - rect.top;
  // Tolerância de meio pixel de CSS para bordas fracionárias (layout sub-pixel).
  const EPS = 0.5;
  const outside = relX < -EPS || relY < -EPS || relX > rect.width + EPS || relY > rect.height + EPS;
  if (outside && !options.clampOutside) return null;

  const scaleX = frame.width / rect.width;
  const scaleY = frame.height / rect.height;
  const maxX = Math.max(0, Math.round(frame.width) - 1);
  const maxY = Math.max(0, Math.round(frame.height) - 1);
  const x = Math.min(maxX, Math.max(0, Math.floor(relX * scaleX)));
  const y = Math.min(maxY, Math.max(0, Math.floor(relY * scaleY)));
  return { x, y };
}

/** Inverso: pixel do aparelho → posição (CSS px) dentro da caixa. Usado para desenhar marcações. */
export function mapDeviceToBox(point: DevicePoint, box: Size, frame: Size): { px: number; py: number } | null {
  const rect = containedRect(box, frame);
  if (!rect) return null;
  return {
    px: rect.left + ((point.x + 0.5) / frame.width) * rect.width,
    py: rect.top + ((point.y + 0.5) / frame.height) * rect.height,
  };
}

/**
 * Tamanho do APARELHO a usar com a imagem exibida.
 *
 * O contrato garante que `FrameInfo.width/height` são pixels do aparelho, mas não diz o que os cabeçalhos
 * `X-Frame-Width/Height` medem (poderiam ser os pixels do JPEG reduzido). Por isso a ordem é:
 *   1. o FrameInfo cujo `id` é o MESMO do frame exibido;
 *   2. sem correspondência: o FrameInfo mais recente, desde que a proporção bata com a dos cabeçalhos
 *      (mesma orientação → mesmas dimensões do aparelho);
 *   3. proporção diferente (o aparelho girou) → cabeçalhos; e, na falta deles, o FrameInfo mais recente.
 */
export function resolveFrameSize(match: Size | null, header: Partial<Size> | null, latest: Size | null): Size | null {
  const valid = (s: Partial<Size> | null): s is Size => !!s && isPositive(s.width ?? Number.NaN) && isPositive(s.height ?? Number.NaN);
  if (valid(match)) return { width: match.width, height: match.height };
  if (valid(header) && valid(latest)) {
    const sameAspect = Math.abs(latest.width / latest.height - header.width / header.height) < 0.02;
    const pick = sameAspect ? latest : header;
    return { width: pick.width, height: pick.height };
  }
  if (valid(header)) return { width: header.width, height: header.height };
  if (valid(latest)) return { width: latest.width, height: latest.height };
  return null;
}

/** Distância euclidiana em CSS px — usada para distinguir toque de arrasto. */
export function distance(ax: number, ay: number, bx: number, by: number): number {
  return Math.hypot(bx - ax, by - ay);
}
