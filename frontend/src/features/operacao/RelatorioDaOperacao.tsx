import { Download, FileText } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { toast } from '../../store/toasts';
import styles from './Operacao.module.css';
import { apiOperacoes } from './api';
import { type LeituraDoAprendizado } from './aprendizadoDaOperacao';
import { type Operacao } from './modelo';
import { montarRelatorio, relatorioEmMarkdown, type RelatorioDaOperacao as Relatorio } from './relatorio';

/** O nome do arquivo sem nada que um sistema de arquivos estranhe (o id da operação vem do servidor). */
export const nomeDoArquivo = (id: string, extensao: 'md' | 'json'): string => `operacao-${id.replace(/[^A-Za-z0-9_-]+/g, '_')}.${extensao}`;

function baixar(nome: string, conteudo: string, tipo: string): void {
  const url = URL.createObjectURL(new Blob([conteudo], { type: `${tipo};charset=utf-8` }));
  const a = document.createElement('a');
  a.href = url;
  a.download = nome;
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const duracao = (ms: number | null): string => {
  if (ms === null) return 'não medido';
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
};

/** 31.197: de onde veio o relatório e, quando o central o montou, a resposta objetiva, os critérios, a latência e o custo por peça. */
function ResumoDoRelatorio({ r, motivoDaReserva }: { r: Relatorio; motivoDaReserva: string | null }) {
  const crit = r.criterios;
  const conta = (v: 'sim' | 'nao' | 'nao_medido') => (crit ?? []).filter((c) => c.nesta_operacao === v).length;
  return (
    <div data-resumo-do-relatorio>
      <p className={styles.mudo} data-fonte={r.fonte} role="status">
        {r.fonte === 'servidor' ? 'Relatório montado pelo central.' : `Relatório montado pelo painel, do estado da operação: ${motivoDaReserva ?? 'o central não o entregou'}`}
      </p>
      {r.identidades && r.identidades.executam_hoje !== null && r.identidades.solicitadas !== null ? (
        <p data-identidades><strong>{r.identidades.executam_hoje} de {r.identidades.solicitadas}</strong> identidades executam hoje.</p>
      ) : null}
      {crit ? (
        <p className={styles.mudo} data-criterios>
          Critérios do diagnóstico: {crit.length}; valeram nesta operação {conta('sim')}, não valeram {conta('nao')}, {conta('nao_medido')} não medidos.
        </p>
      ) : null}
      {r.latencia ? (
        <p className={styles.mudo} data-latencia-do-relatorio>
          Duração mediana por agente: {duracao(r.latencia.duracao_mediana_ms)}
          {r.latencia.mais_lento ? `; o mais lento, ${r.latencia.mais_lento.agente}, levou ${duracao(r.latencia.mais_lento.duracao_ms)}` : ''}.
        </p>
      ) : null}
      {r.fonte === 'servidor' ? (
        <p className={styles.mudo} data-custo-por-peca>
          Custo por peça: {r.custo.por_peca_usd === null ? 'sem nenhuma ação executada e verificada' : `US$ ${r.custo.por_peca_usd.toFixed(4)}`}.
        </p>
      ) : null}
    </div>
  );
}

/**
 * "Relatório" (31.162): o que a operação fez, por agente e consolidado, em Markdown (para ler) e em JSON (para guardar ou
 * comparar). 31.197: vem do central quando ele o monta (`GET /api/operacoes/{id}/relatorio`, v1.111); a montagem aqui, do que a
 * leitura da operação trouxe, é só a reserva. Nada vai ao servidor.
 */
export function RelatorioDaOperacao({ op, onFechar }: { op: Operacao; onFechar: () => void }) {
  // 31.197: o relatório vem do central (v1.111) quando ele o oferece; sem isso, o painel lê o aprendizado e o monta (a reserva). Uma leitura
  // ao abrir o diálogo, sem IA; só depois dela os arquivos saem completos.
  const [leitura, setLeitura] = useState<{ relatorio: Relatorio; motivoDaReserva: string | null } | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    void (async () => {
      const doCentral = await apiOperacoes.relatorio(op.id, ctl.signal);
      if (ctl.signal.aborted) return;
      if (doCentral.situacao === 'central') { setLeitura({ relatorio: doCentral.relatorio, motivoDaReserva: null }); return; }
      const aprendizado: LeituraDoAprendizado = await apiOperacoes.aprendizado(op.id, ctl.signal);
      if (!ctl.signal.aborted) setLeitura({ relatorio: montarRelatorio(op, new Date(), aprendizado), motivoDaReserva: doCentral.motivo });
    })();
    return () => ctl.abort();
    // `op` é o estado de agora; reler o central a cada render do pai não faz sentido: o relatório é de quando o diálogo abre.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [op.id]);
  const relatorio = leitura?.relatorio ?? null;
  const aviso = relatorio === null ? 'Lendo o relatório da operação.' : null;
  const agentes = op.alvos.length;
  return (
    <Dialog open onClose={onFechar} title="Relatório da operação" icon={FileText} size="md"
            footer={<Button variant="ghost" onClick={onFechar}>Fechar</Button>}>
      <p>
        Um arquivo com, por agente, os 14 estágios com a hora, o motivo de quem parou, o conhecimento usado, o texto gerado e a ação final
        com a conferência; e o consolidado com a capacidade, os custos, as falhas por motivo, os textos irmãos e as 10 perguntas do dono sobre
        o que a operação ensinou. Os agentes aparecem pelo rótulo da persona, sem a conta.
      </p>
      <p className={styles.mudo}>{agentes} {agentes === 1 ? 'agente' : 'agentes'} · estado de agora: o relatório não se atualiza sozinho.</p>
      {leitura ? <ResumoDoRelatorio r={leitura.relatorio} motivoDaReserva={leitura.motivoDaReserva} /> : null}
      {relatorio && !relatorio.aprendizado.disponivel ? <p className={styles.mudo}>Aprendizado não disponível: {relatorio.aprendizado.motivo}</p> : null}
      <div className={styles.acoesDoRelatorio}>
        <Button variant="primary" icon={Download} disabledReason={aviso}
                onClick={() => { if (!relatorio) return; baixar(nomeDoArquivo(op.id, 'md'), relatorioEmMarkdown(relatorio), 'text/markdown'); toast({ tone: 'success', title: 'Relatório em Markdown baixado' }); }}>
          Baixar Markdown
        </Button>
        <Button variant="outline" icon={Download} disabledReason={aviso}
                onClick={() => { if (!relatorio) return; baixar(nomeDoArquivo(op.id, 'json'), `${JSON.stringify(relatorio, null, 2)}\n`, 'application/json'); toast({ tone: 'success', title: 'Relatório em JSON baixado' }); }}>
          Baixar JSON
        </Button>
      </div>
    </Dialog>
  );
}
