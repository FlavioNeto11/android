import { Download, FileText } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { toast } from '../../store/toasts';
import styles from './Operacao.module.css';
import { apiOperacoes } from './api';
import { type LeituraDoAprendizado } from './aprendizadoDaOperacao';
import { type Operacao } from './modelo';
import { montarRelatorio, relatorioEmMarkdown } from './relatorio';

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

/**
 * "Relatório" (31.162): o que a operação fez, por agente e consolidado, em Markdown (para ler) e em JSON (para guardar ou
 * comparar). Montado aqui, do que a leitura da operação trouxe; nada vai ao servidor.
 */
export function RelatorioDaOperacao({ op, onFechar }: { op: Operacao; onFechar: () => void }) {
  // O aprendizado é lido uma vez ao abrir o diálogo (uma leitura, sem IA); só depois dela os arquivos saem completos.
  const [aprendizado, setAprendizado] = useState<LeituraDoAprendizado | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    void apiOperacoes.aprendizado(op.id, ctl.signal).then((l) => { if (!ctl.signal.aborted) setAprendizado(l); });
    return () => ctl.abort();
  }, [op.id]);
  const relatorio = useMemo(() => (aprendizado ? montarRelatorio(op, new Date(), aprendizado) : null), [op, aprendizado]);
  const aviso = relatorio === null ? 'Lendo o aprendizado da operação.' : null;
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
