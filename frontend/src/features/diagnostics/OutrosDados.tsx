import type { ReactNode } from 'react';
import { Disclosure } from '../../components/Disclosure';
import { CodeBlock, JsonTree, KvList, KvRow } from '../../components/JsonTree';
import { RecordTable } from '../../components/RecordTable';
import { formatDecimal, humanizeKey, isRecord, scalarToText } from '../../lib/format';
import { formatDateTime } from '../../lib/time';
import styles from './Diagnostics.module.css';
import {
  type RodadaDeEscala, type SondaDeImagem, lerSondasDeImagem, lerTesteDeEscala, matrizPorAparelho, nomeCurtoDaImagem,
} from './outros';

/** Título e explicação de cada chave conhecida; a desconhecida entra com o nome "humanizado" e a árvore genérica. */
const SECOES: Record<string, { titulo: string; descricao?: string }> = {
  measured_on: { titulo: 'Onde foi medido' },
  sdk: { titulo: 'SDK do Android', descricao: 'Onde o SDK está, que imagens de sistema estão instaladas e qual o parque usa.' },
  scale_test: {
    titulo: 'Teste de escala',
    descricao: 'Aparelhos ligados em levas: quantos subiram, quanto a leva levou e como ficaram a CPU e a memória do host.',
  },
  image_probes: {
    titulo: 'Imagens de sistema medidas',
    descricao: 'Um boot de cada imagem: a RAM que o Android enxerga, a memória que o emulador ocupa no host e o primeiro boot.',
  },
  host_script: { titulo: 'Levantamento do host (script)', descricao: 'O que o script de diagnóstico do host coletou da última vez que rodou.' },
};

/** Rótulos das chaves aninhadas (SDK e script do host), somados aos do diagnóstico. */
const ROTULOS: Record<string, string> = {
  collected_at: 'Coletado em', measured_on: 'Máquina medida', is_admin: 'Rodou como administrador',
  logical: 'Núcleos lógicos', ram_total_gb: 'Memória total (GB)', ram_free_gb: 'Memória livre (GB)',
  commit_used_gb: 'Memória comprometida (GB)', commit_limit_gb: 'Limite de memória comprometida (GB)',
  disks: 'Discos', drive: 'Unidade', free_gb: 'Livre (GB)', used_gb: 'Usado (GB)',
  top_memory_processes: 'Processos que mais usam memória', pid: 'PID', ws_gb: 'Memória em uso (GB)',
  hypervisor_present: 'Hipervisor presente', hypervisorlaunchtype: 'Início do hipervisor',
  windows_features: 'Recursos do Windows', accel_check: 'Verificação de aceleração do emulador', advice: 'Conclusão',
  tools: 'Ferramentas', java: 'Java', adb: 'adb', emulator: 'Emulador', sdk_root: 'Pasta do SDK', sdk_found: 'SDK encontrado',
  estimate: 'Estimativa', per_instance_gb: 'Memória por aparelho (GB)', note: 'Observação', fits_now: 'Cabem agora',
  root: 'Pasta do SDK', found: 'Encontrado', system_images: 'Imagens de sistema instaladas',
  configured_image: 'Imagem configurada', configured_image_installed: 'Imagem configurada instalada',
  override_images: 'Imagem própria por aparelho', installed: 'Instalada',
};

const CHAVES_DE_DATA = new Set(['collected_at']);
/** Identificadores numéricos: "5392", não "5.392". */
const CHAVES_SEM_MILHAR = new Set(['pid']);

function celula(chave: string, valor: unknown): ReactNode | undefined {
  return CHAVES_SEM_MILHAR.has(chave) && typeof valor === 'number' ? String(valor) : undefined;
}

function fmt(n: number | null | undefined, casas = 1): string {
  if (n === null || n === undefined) return '—';
  return casas === 0 ? scalarToText(Math.round(n)) : formatDecimal(n);
}

/**
 * "Outros dados" do Diagnóstico. Era uma árvore chave/valor aninhada sem fim: o teste de escala repetia a lista de
 * aparelhos em cada leva e as chaves vinham em inglês cru. Agora cada chave conhecida tem um bloco próprio (tabelas
 * para o que é lista, chave/valor em português para o resto) e a desconhecida continua visível pela árvore genérica.
 */
export function OutrosDados({ data, chaves, labels }: { data: Record<string, unknown>; chaves: string[]; labels: Record<string, string> }) {
  const rotulos = { ...labels, ...ROTULOS };
  return (
    <div className={styles.outros}>
      {chaves.map((k) => {
        const secao = SECOES[k];
        const tituloId = `diag-outros-${k}`;
        return (
          <section key={k} className={styles.outroBloco} aria-labelledby={tituloId}>
            <h4 id={tituloId} className={styles.outroTitulo}>{secao?.titulo ?? humanizeKey(k)}</h4>
            {secao?.descricao ? <p className={styles.meta}>{secao.descricao}</p> : null}
            <ConteudoDaChave chave={k} valor={data[k]} rotulos={rotulos} />
          </section>
        );
      })}
    </div>
  );
}

function ConteudoDaChave({ chave, valor, rotulos }: { chave: string; valor: unknown; rotulos: Record<string, string> }) {
  if (chave === 'scale_test') {
    const rodadas = lerTesteDeEscala(valor);
    if (rodadas) return <TesteDeEscala rodadas={rodadas} />;
  }
  if (chave === 'image_probes') {
    const sondas = lerSondasDeImagem(valor);
    if (sondas) return <SondasDeImagem sondas={sondas} />;
  }
  return <Estruturado valor={valor} rotulos={rotulos} />;
}

// ---------------------------------------------------------------- renderização genérica, legível

function Lista({ itens }: { itens: unknown[] }) {
  return (
    <ul className={styles.outroLista}>
      {itens.map((x, i) => <li key={i} className={typeof x === 'string' ? 'mono' : undefined}>{scalarToText(x)}</li>)}
    </ul>
  );
}

/** Texto de várias linhas (ex.: a saída do `emulator -accel-check`) em bloco de código; o resto em linha. */
function Escalar({ chave, valor }: { chave?: string; valor: unknown }) {
  if (typeof valor === 'string' && /[\r\n]/.test(valor)) return <CodeBlock value={valor.replace(/\r\n/g, '\n').trim()} />;
  if (chave && CHAVES_DE_DATA.has(chave) && typeof valor === 'string') return <>{formatDateTime(valor)}</>;
  if (chave && CHAVES_SEM_MILHAR.has(chave) && typeof valor === 'number') return <>{String(valor)}</>;
  return <>{scalarToText(valor)}</>;
}

const eEscalar = (v: unknown) => v === null || v === undefined || typeof v !== 'object';

/**
 * Objeto → chave/valor com os escalares primeiro; cada lista ou objeto aninhado ganha um subtítulo próprio embaixo:
 * lista de objetos vira tabela, lista de textos uma linha por item. Três níveis; depois disso, a árvore genérica.
 */
function Estruturado({ valor, rotulos, nivel = 0 }: { valor: unknown; rotulos: Record<string, string>; nivel?: number }): ReactNode {
  if (eEscalar(valor)) return <p><Escalar valor={valor} /></p>;
  if (nivel >= 3) return <JsonTree value={valor} labels={rotulos} />;
  if (Array.isArray(valor)) {
    if (valor.length === 0) return <p className={styles.meta}>Nenhum item.</p>;
    if (valor.every(eEscalar)) return <Lista itens={valor} />;
    if (valor.every(isRecord)) return <RecordTable rows={valor} labels={rotulos} renderCell={celula} />;
    return <JsonTree value={valor} labels={rotulos} />;
  }
  if (!isRecord(valor)) return <JsonTree value={valor} labels={rotulos} />;
  const entradas = Object.entries(valor);
  const escalares = entradas.filter(([, v]) => eEscalar(v) && !(typeof v === 'string' && /[\r\n]/.test(v)));
  const demais = entradas.filter(([k]) => !escalares.some(([e]) => e === k));
  return (
    <div className={styles.outroEstruturado}>
      {escalares.length > 0 ? (
        <KvList>
          {escalares.map(([k, v]) => <KvRow key={k} label={rotulos[k] ?? humanizeKey(k)}><Escalar chave={k} valor={v} /></KvRow>)}
        </KvList>
      ) : null}
      {demais.map(([k, v]) => (
        <div key={k} className={styles.outroSub}>
          <p className={styles.outroSubTitulo}>{rotulos[k] ?? humanizeKey(k)}</p>
          {eEscalar(v) ? <Escalar chave={k} valor={v} /> : <Estruturado valor={v} rotulos={rotulos} nivel={nivel + 1} />}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- teste de escala

function TesteDeEscala({ rodadas }: { rodadas: RodadaDeEscala[] }) {
  const matriz = matrizPorAparelho(rodadas);
  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption className="sr-only">Rodadas do teste de escala</caption>
          <thead>
            <tr>
              <th scope="col" className={styles.num}>Pedidos</th>
              <th scope="col" className={styles.num}>No ar</th>
              <th scope="col" className={styles.num}>Tempo da leva (s)</th>
              <th scope="col" className={styles.num}>CPU do host (%)</th>
              <th scope="col" className={styles.num}>Memória livre (GB)</th>
              <th scope="col" className={styles.num}>Memória usada (%)</th>
              <th scope="col">Fora do ar</th>
              <th scope="col">IA</th>
              <th scope="col">Quando</th>
            </tr>
          </thead>
          <tbody>
            {rodadas.map((r, i) => (
              <tr key={i}>
                <td className={styles.num}>{fmt(r.alvo, 0)}</td>
                <td className={styles.num}>{fmt(r.noAr, 0)}</td>
                <td className={styles.num}>{fmt(r.tempoDaLeva, 0)}</td>
                <td className={styles.num}>{fmt(r.cpu)}</td>
                <td className={styles.num}>{fmt(r.memLivreGb)}</td>
                <td className={styles.num}>{fmt(r.memUsadaPct)}</td>
                <td>{r.foraDoAr.length > 0 ? <span className={styles.outroAviso}>{r.foraDoAr.join(', ')}</span> : <span className={styles.meta}>nenhum</span>}</td>
                <td>{r.ia ? `${r.ia}${r.iaSimulada ? ' (simulada)' : ''}` : '—'}</td>
                <td className={styles.outroQuando}>{r.quando ? formatDateTime(r.quando) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {matriz.length > 0 ? (
        <Disclosure bare summary={`Por aparelho: boot e RAM do emulador em cada leva (${matriz.length} aparelhos)`}>
          {() => (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <caption className="sr-only">Boot e RAM do emulador por aparelho em cada leva do teste de escala</caption>
                <thead>
                  <tr>
                    <th scope="col">Aparelho</th>
                    <th scope="col" className={styles.num}>Boot (s)</th>
                    {rodadas.map((r, i) => (
                      <th key={i} scope="col" className={styles.num}>RAM com {fmt(r.alvo ?? i + 1, 0)} (MB)</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {matriz.map((l) => (
                    <tr key={l.id}>
                      <th scope="row" className={styles.outroAparelho}>{l.id}</th>
                      <td className={styles.num}>{fmt(l.boot)}</td>
                      {l.ramMb.map((v, i) => <td key={i} className={styles.num}>{v === undefined ? <span className={styles.meta}>—</span> : fmt(v, 0)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Disclosure>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------- imagens medidas

function SondasDeImagem({ sondas }: { sondas: SondaDeImagem[] }) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className="sr-only">Imagens de sistema medidas</caption>
        <thead>
          <tr>
            <th scope="col">Imagem</th>
            <th scope="col" className={styles.num}>Android</th>
            <th scope="col" className={styles.num}>RAM pedida (MB)</th>
            <th scope="col" className={styles.num}>RAM que o Android vê (GB)</th>
            <th scope="col" className={styles.num}>Emulador em uso (GB)</th>
            <th scope="col" className={styles.num}>Emulador, privada (GB)</th>
            <th scope="col" className={styles.num}>Primeiro boot (s)</th>
          </tr>
        </thead>
        <tbody>
          {sondas.map((s, i) => (
            <tr key={`${s.imagem ?? 'imagem'}-${i}`}>
              <td className="mono" title={s.imagem ?? undefined}>{s.imagem ? nomeCurtoDaImagem(s.imagem) : '—'}</td>
              <td className={styles.num}>{s.android ?? '—'}</td>
              <td className={styles.num}>{fmt(s.ramPedidaMb, 0)}</td>
              <td className={styles.num} title={s.ramDoAndroidBruta ?? undefined}>
                {s.ramDoAndroidGb !== null ? fmt(s.ramDoAndroidGb) : s.ramDoAndroidBruta ?? '—'}
              </td>
              <td className={styles.num}>{fmt(s.emUsoGb)}</td>
              <td className={styles.num}>{fmt(s.privadaGb)}</td>
              <td className={styles.num}>{fmt(s.primeiroBootS, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
