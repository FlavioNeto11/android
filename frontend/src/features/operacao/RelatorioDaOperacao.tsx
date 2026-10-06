import { Download, FileText } from 'lucide-react';
import { useMemo } from 'react';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { toast } from '../../store/toasts';
import styles from './Operacao.module.css';
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
  const relatorio = useMemo(() => montarRelatorio(op), [op]);
  const agentes = relatorio.agentes.length;
  return (
    <Dialog open onClose={onFechar} title="Relatório da operação" icon={FileText} size="md"
            footer={<Button variant="ghost" onClick={onFechar}>Fechar</Button>}>
      <p>
        Um arquivo com, por agente, os 14 estágios com a hora, o motivo de quem parou, o conhecimento usado, o texto gerado e a ação final
        com a conferência; e o consolidado com a capacidade, os custos, as falhas por motivo e os textos irmãos. Os agentes aparecem pelo
        rótulo da persona, sem a conta.
      </p>
      <p className={styles.mudo}>{agentes} {agentes === 1 ? 'agente' : 'agentes'} · estado de agora: o relatório não se atualiza sozinho.</p>
      <div className={styles.acoesDoRelatorio}>
        <Button variant="primary" icon={Download}
                onClick={() => { baixar(nomeDoArquivo(op.id, 'md'), relatorioEmMarkdown(relatorio), 'text/markdown'); toast({ tone: 'success', title: 'Relatório em Markdown baixado' }); }}>
          Baixar Markdown
        </Button>
        <Button variant="outline" icon={Download}
                onClick={() => { baixar(nomeDoArquivo(op.id, 'json'), `${JSON.stringify(relatorio, null, 2)}\n`, 'application/json'); toast({ tone: 'success', title: 'Relatório em JSON baixado' }); }}>
          Baixar JSON
        </Button>
      </div>
    </Dialog>
  );
}
