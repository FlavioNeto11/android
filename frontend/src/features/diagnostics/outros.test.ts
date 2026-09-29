import { describe, expect, it } from 'vitest';
import { lerSondasDeImagem, lerTesteDeEscala, matrizPorAparelho, memTotalGb, nomeCurtoDaImagem } from './outros';

/**
 * "Outros dados" do Diagnóstico: leitura do `scale_test` e das `image_probes` no formato do `GET /api/diagnostics` real
 * do central (29/09, encurtado). Prova `simulated`.
 */
const ESCALA = [
  {
    target: 1, online: 1, ts: '2026-09-24T12:16:24', boot_wall_seconds: 5.0,
    boot_seconds_each: [{ id: 'android-01', boot_seconds: 24.2 }], not_online: [],
    host_cpu_percent: 12.1, mem_available_gb: 30.2, mem_used_percent: 52.5,
    emulator_rss_mb: [{ id: 'android-01', rss_mb: 583.0, cpu: 0.0 }], ai_provider: 'anthropic', ai_simulated: false,
  },
  {
    target: 10, online: 9, ts: '2026-09-24T12:33:51', boot_wall_seconds: 626.0,
    boot_seconds_each: [{ id: 'android-01', boot_seconds: 24.2 }, { id: 'android-10', boot_seconds: 23.1 },
                        { id: 'android-2', boot_seconds: null }],
    not_online: ['android-10'],
    host_cpu_percent: 14.2, mem_available_gb: 20.6, mem_used_percent: 67.5,
    // Nem todo aparelho da leva aparece aqui (o real listou 8 de 10).
    emulator_rss_mb: [{ id: 'android-01', rss_mb: 836.0, cpu: 0.0 }], ai_provider: 'anthropic', ai_simulated: false,
  },
];

describe('lerTesteDeEscala', () => {
  it('lê cada leva com os campos em português', () => {
    const r = lerTesteDeEscala(ESCALA);
    expect(r).toHaveLength(2);
    expect(r?.[1]).toMatchObject({ alvo: 10, noAr: 9, tempoDaLeva: 626, cpu: 14.2, memLivreGb: 20.6, memUsadaPct: 67.5,
                                    foraDoAr: ['android-10'], ia: 'anthropic', iaSimulada: false });
  });

  it('formato inesperado devolve null (a tela cai para a árvore genérica)', () => {
    expect(lerTesteDeEscala({ target: 1 })).toBeNull();
    expect(lerTesteDeEscala([])).toBeNull();
    expect(lerTesteDeEscala([1, 2])).toBeNull();
  });
});

describe('matrizPorAparelho', () => {
  it('uma linha por aparelho em ordem natural, o último boot medido e a RAM de cada leva', () => {
    const m = matrizPorAparelho(lerTesteDeEscala(ESCALA) ?? []);
    expect(m.map((l) => l.id)).toEqual(['android-01', 'android-2', 'android-10']);
    expect(m[0]).toEqual({ id: 'android-01', boot: 24.2, ramMb: [583, 836] });
    // Estava na leva sem tempo medido: boot null. Fora da lista de RAM da leva: undefined (célula "—").
    expect(m[1]).toEqual({ id: 'android-2', boot: null, ramMb: [undefined, undefined] });
    expect(m[2]?.boot).toBe(23.1);
  });
});

describe('sondas de imagem', () => {
  it('converte o MemTotal do convidado para GB e guarda o texto cru', () => {
    const [s] = lerSondasDeImagem([{
      image: 'system-images;android-34;aosp_atd;x86_64', android_release: '14', requested_ram_mb: 1536,
      guest_memtotal: 'MemTotal:        2534552 kB', qemu_ws_gb: 2.78, qemu_private_gb: 3.32, first_boot_s: 68.0,
    }]) ?? [];
    expect(s?.ramDoAndroidGb).toBeCloseTo(2.417, 2);
    expect(s).toMatchObject({ android: '14', ramPedidaMb: 1536, emUsoGb: 2.78, privadaGb: 3.32, primeiroBootS: 68,
                              ramDoAndroidBruta: 'MemTotal:        2534552 kB' });
  });

  it('MemTotal fora do formato não vira número', () => {
    expect(memTotalGb('2,4 GB')).toBeNull();
    expect(memTotalGb(null)).toBeNull();
  });

  it('nome curto da imagem, sem o prefixo system-images', () => {
    expect(nomeCurtoDaImagem('system-images;android-34;google_apis_playstore;x86_64')).toBe('android-34 · google_apis_playstore · x86_64');
    expect(nomeCurtoDaImagem('imagem-livre')).toBe('imagem-livre');
  });
});
