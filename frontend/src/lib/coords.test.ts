import { describe, expect, it } from 'vitest';
import { containedRect, mapDeviceToBox, mapPointToDevice, resolveFrameSize } from './coords';

const PORTRAIT = { width: 1080, height: 2400 };
const LANDSCAPE = { width: 2400, height: 1080 };

describe('containedRect', () => {
  it('ocupa a caixa inteira quando a proporção é idêntica', () => {
    expect(containedRect({ width: 540, height: 1200 }, PORTRAIT)).toEqual({ left: 0, top: 0, width: 540, height: 1200 });
  });

  it('centraliza horizontalmente (pillarbox) em caixa mais larga', () => {
    // escala = 1200/2400 = 0.5 → imagem 540×1200 dentro de 1000×1200 → margens de 230 px
    expect(containedRect({ width: 1000, height: 1200 }, PORTRAIT)).toEqual({ left: 230, top: 0, width: 540, height: 1200 });
  });

  it('centraliza verticalmente (letterbox) em caixa mais alta', () => {
    // escala = 540/1080 = 0.5 → imagem 540×1200 dentro de 540×1600 → margens de 200 px
    expect(containedRect({ width: 540, height: 1600 }, PORTRAIT)).toEqual({ left: 0, top: 200, width: 540, height: 1200 });
  });

  it('devolve null para tamanhos inválidos', () => {
    expect(containedRect({ width: 0, height: 100 }, PORTRAIT)).toBeNull();
    expect(containedRect({ width: 100, height: 100 }, { width: 0, height: 0 })).toBeNull();
    expect(containedRect({ width: Number.NaN, height: 100 }, PORTRAIT)).toBeNull();
  });
});

describe('mapPointToDevice', () => {
  describe('encaixe exato (sem margens)', () => {
    const box = { width: 540, height: 1200 };

    it('mapeia o canto superior esquerdo para (0,0)', () => {
      expect(mapPointToDevice(0, 0, box, PORTRAIT)).toEqual({ x: 0, y: 0 });
    });

    it('mapeia o centro', () => {
      expect(mapPointToDevice(270, 600, box, PORTRAIT)).toEqual({ x: 540, y: 1200 });
    });

    it('mapeia um ponto arbitrário pela escala 2×', () => {
      expect(mapPointToDevice(100.25, 333.5, box, PORTRAIT)).toEqual({ x: 200, y: 667 });
    });
  });

  describe('pillarbox (margens à esquerda e à direita)', () => {
    const box = { width: 1000, height: 1200 }; // imagem ocupa x ∈ [230, 770]

    it('desconta a margem esquerda', () => {
      expect(mapPointToDevice(230, 0, box, PORTRAIT)).toEqual({ x: 0, y: 0 });
      expect(mapPointToDevice(500, 600, box, PORTRAIT)).toEqual({ x: 540, y: 1200 });
      expect(mapPointToDevice(330, 100, box, PORTRAIT)).toEqual({ x: 200, y: 200 });
    });

    it('rejeita cliques nas margens laterais', () => {
      expect(mapPointToDevice(100, 600, box, PORTRAIT)).toBeNull();
      expect(mapPointToDevice(229, 600, box, PORTRAIT)).toBeNull();
      expect(mapPointToDevice(771, 600, box, PORTRAIT)).toBeNull();
      expect(mapPointToDevice(999, 10, box, PORTRAIT)).toBeNull();
    });
  });

  describe('letterbox (margens em cima e embaixo)', () => {
    const box = { width: 540, height: 1600 }; // imagem ocupa y ∈ [200, 1400]

    it('desconta a margem superior', () => {
      expect(mapPointToDevice(0, 200, box, PORTRAIT)).toEqual({ x: 0, y: 0 });
      expect(mapPointToDevice(270, 800, box, PORTRAIT)).toEqual({ x: 540, y: 1200 });
    });

    it('rejeita cliques nas margens superior e inferior', () => {
      expect(mapPointToDevice(270, 50, box, PORTRAIT)).toBeNull();
      expect(mapPointToDevice(270, 199, box, PORTRAIT)).toBeNull();
      expect(mapPointToDevice(270, 1401, box, PORTRAIT)).toBeNull();
      expect(mapPointToDevice(270, 1599, box, PORTRAIT)).toBeNull();
    });
  });

  describe('frames em paisagem', () => {
    it('faz letterbox de um frame paisagem dentro de uma caixa retrato', () => {
      const box = { width: 600, height: 1000 }; // escala 0.25 → 600×270, topo em 365
      expect(containedRect(box, LANDSCAPE)).toEqual({ left: 0, top: 365, width: 600, height: 270 });
      expect(mapPointToDevice(300, 500, box, LANDSCAPE)).toEqual({ x: 1200, y: 540 });
      expect(mapPointToDevice(0, 365, box, LANDSCAPE)).toEqual({ x: 0, y: 0 });
      expect(mapPointToDevice(300, 100, box, LANDSCAPE)).toBeNull();
      expect(mapPointToDevice(300, 700, box, LANDSCAPE)).toBeNull();
    });

    it('faz pillarbox de um frame paisagem em caixa ainda mais larga', () => {
      const box = { width: 1600, height: 540 }; // escala 0.5 → 1200×540, esquerda em 200
      expect(mapPointToDevice(200, 0, box, LANDSCAPE)).toEqual({ x: 0, y: 0 });
      expect(mapPointToDevice(800, 270, box, LANDSCAPE)).toEqual({ x: 1200, y: 540 });
      expect(mapPointToDevice(150, 270, box, LANDSCAPE)).toBeNull();
    });
  });

  describe('imagem reduzida (JPEG menor que o aparelho)', () => {
    it('usa FrameInfo e não depende do tamanho natural do JPEG', () => {
      // O JPEG poderia ter 360×800; a função nem recebe esse dado: só a caixa e o FrameInfo (1080×2400).
      const box = { width: 360, height: 800 }; // escala 1/3
      expect(mapPointToDevice(180, 400, box, PORTRAIT)).toEqual({ x: 540, y: 1200 });
      expect(mapPointToDevice(120, 100, box, PORTRAIT)).toEqual({ x: 360, y: 300 });
    });

    it('funciona também quando a caixa é menor que a miniatura e há margens', () => {
      const box = { width: 300, height: 400 }; // escala = 400/2400 → imagem 180×400, esquerda em 60
      expect(mapPointToDevice(60, 0, box, PORTRAIT)).toEqual({ x: 0, y: 0 });
      expect(mapPointToDevice(150, 200, box, PORTRAIT)).toEqual({ x: 540, y: 1200 });
      expect(mapPointToDevice(30, 200, box, PORTRAIT)).toBeNull();
    });
  });

  describe('limites (clamp)', () => {
    const box = { width: 540, height: 1200 };

    it('limita a borda direita/inferior a width-1 / height-1', () => {
      expect(mapPointToDevice(540, 1200, box, PORTRAIT)).toEqual({ x: 1079, y: 2399 });
      expect(mapPointToDevice(539.9, 1199.9, box, PORTRAIT)).toEqual({ x: 1079, y: 2399 });
    });

    it('tolera meio pixel de erro sub-pixel nas bordas, mas não mais que isso', () => {
      expect(mapPointToDevice(-0.4, -0.4, box, PORTRAIT)).toEqual({ x: 0, y: 0 });
      expect(mapPointToDevice(540.4, 1200.4, box, PORTRAIT)).toEqual({ x: 1079, y: 2399 });
      expect(mapPointToDevice(-2, 10, box, PORTRAIT)).toBeNull();
      expect(mapPointToDevice(10, 1203, box, PORTRAIT)).toBeNull();
    });

    it('com clampOutside traz o ponto para a borda mais próxima em vez de rejeitar', () => {
      const wide = { width: 1000, height: 1200 }; // imagem em x ∈ [230, 770]
      expect(mapPointToDevice(50, 600, wide, PORTRAIT, { clampOutside: true })).toEqual({ x: 0, y: 1200 });
      expect(mapPointToDevice(990, -80, wide, PORTRAIT, { clampOutside: true })).toEqual({ x: 1079, y: 0 });
      expect(mapPointToDevice(500, 5000, wide, PORTRAIT, { clampOutside: true })).toEqual({ x: 540, y: 2399 });
    });

    it('sempre devolve inteiros dentro dos limites do aparelho', () => {
      const odd = { width: 377, height: 811 };
      for (let px = 0; px <= odd.width; px += 7.3) {
        for (let py = 0; py <= odd.height; py += 11.7) {
          const p = mapPointToDevice(px, py, odd, PORTRAIT);
          if (!p) continue;
          expect(Number.isInteger(p.x)).toBe(true);
          expect(Number.isInteger(p.y)).toBe(true);
          expect(p.x).toBeGreaterThanOrEqual(0);
          expect(p.x).toBeLessThanOrEqual(1079);
          expect(p.y).toBeGreaterThanOrEqual(0);
          expect(p.y).toBeLessThanOrEqual(2399);
        }
      }
    });
  });

  describe('entradas inválidas', () => {
    it('devolve null quando a caixa ou o frame não têm tamanho', () => {
      expect(mapPointToDevice(10, 10, { width: 0, height: 0 }, PORTRAIT)).toBeNull();
      expect(mapPointToDevice(10, 10, { width: 100, height: 100 }, { width: 0, height: 2400 })).toBeNull();
      expect(mapPointToDevice(Number.NaN, 10, { width: 100, height: 100 }, PORTRAIT)).toBeNull();
    });
  });
});

describe('resolveFrameSize', () => {
  const device = { width: 1080, height: 2400 };

  it('prefere o FrameInfo do mesmo frame, ignorando cabeçalhos que descrevam o JPEG reduzido', () => {
    expect(resolveFrameSize(device, { width: 540, height: 1200 }, null)).toEqual(device);
  });

  it('sem correspondência, usa o FrameInfo mais recente quando a proporção é a mesma', () => {
    expect(resolveFrameSize(null, { width: 540, height: 1200 }, device)).toEqual(device);
  });

  it('se o aparelho girou (proporção diferente), confia nos cabeçalhos do frame exibido', () => {
    expect(resolveFrameSize(null, { width: 2400, height: 1080 }, device)).toEqual({ width: 2400, height: 1080 });
  });

  it('usa o que houver e devolve null sem nenhuma dimensão válida', () => {
    expect(resolveFrameSize(null, { width: 1080, height: 2400 }, null)).toEqual(device);
    expect(resolveFrameSize(null, null, device)).toEqual(device);
    expect(resolveFrameSize(null, { width: 0, height: 0 }, null)).toBeNull();
    expect(resolveFrameSize(null, null, null)).toBeNull();
  });
});

describe('mapDeviceToBox', () => {
  it('é o inverso aproximado de mapPointToDevice', () => {
    const box = { width: 1000, height: 1200 };
    const back = mapDeviceToBox({ x: 540, y: 1200 }, box, PORTRAIT);
    expect(back).not.toBeNull();
    const again = mapPointToDevice(back!.px, back!.py, box, PORTRAIT);
    expect(again).toEqual({ x: 540, y: 1200 });
  });
});
