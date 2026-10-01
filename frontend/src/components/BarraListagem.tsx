import { LayoutGrid, Rows3, Search, X } from 'lucide-react';
import { useId, type ReactNode } from 'react';
import { cx } from '../lib/format';
import { Select, TextInput } from './Field';
import { Tooltip } from './Tooltip';
import styles from './BarraListagem.module.css';

export interface OpcaoListagem {
  valor: string;
  rotulo: string;
  /** Quantos itens a opção mostraria (com os outros filtros aplicados). */
  contagem?: number;
  /** Dica do chip (hover e foco de teclado), quando o rótulo sozinho não diz o que entra. */
  dica?: string;
}

/** Um filtro da barra: chips (poucas opções, sempre à vista) ou lista (muitas opções). `''` = sem filtro. */
export interface FiltroListagem {
  chave: string;
  rotulo: string;
  tipo: 'chips' | 'lista';
  opcoes: readonly OpcaoListagem[];
  valor: string;
  onChange: (valor: string) => void;
  /** Rótulo da opção "sem filtro" (chip "Todas" ou primeira linha da lista). */
  rotuloTodos: string;
  contagemTodos?: number;
}

interface BarraListagemProps {
  /** Nome do que está sendo listado, para os rótulos acessíveis ("personas", "execuções"). */
  nome: string;
  busca: { valor: string; onChange: (v: string) => void; placeholder: string };
  filtros?: readonly FiltroListagem[];
  ordem?: { valor: string; opcoes: readonly OpcaoListagem[]; onChange: (v: string) => void };
  visao?: { valor: 'cards' | 'tabela'; onChange: (v: 'cards' | 'tabela') => void };
  /** "8 de 14 personas" — o que a lista mostra agora. */
  resumo?: ReactNode;
  /** Presente só quando algum filtro esconde itens. */
  onLimpar?: () => void;
  className?: string;
  /** Coluna estreita (a lista de Execuções, ~340 px): as listas dividem a linha por igual e os chips encolhem. */
  compacta?: boolean;
}

/**
 * Barra de listagem reutilizável (tarefa UX 05): busca, filtros, ordenação e alternância cartões/tabela. É
 * controlada: quem usa guarda o estado (na URL, por `trocarQuery`) e a barra só mostra e avisa. Personas e
 * Execuções usam a mesma, para a mesma coisa estar no mesmo lugar em todas as listas.
 */
export function BarraListagem({ nome, busca, filtros = [], ordem, visao, resumo, onLimpar, className, compacta }: BarraListagemProps) {
  const idBusca = useId();
  const chips = filtros.filter((f) => f.tipo === 'chips');
  const listas = filtros.filter((f) => f.tipo === 'lista');
  return (
    <div className={cx(styles.barra, compacta && styles.compacta, className)} role="search" aria-label={`Buscar e filtrar ${nome}`}>
      <div className={styles.linha}>
        <label className={styles.busca} htmlFor={idBusca}>
          <Search size={15} aria-hidden className={styles.buscaIcone} />
          <span className="sr-only">Buscar {nome}</span>
          <TextInput id={idBusca} type="search" small value={busca.valor} placeholder={busca.placeholder}
                     className={styles.buscaCampo}
                     onChange={(e) => busca.onChange(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Escape' && busca.valor) { e.preventDefault(); busca.onChange(''); } }} />
        </label>
        {listas.map((f) => (
          <label key={f.chave} className={styles.lista}>
            <span className="sr-only">{f.rotulo}</span>
            <Select small value={f.valor} aria-label={f.rotulo} onChange={(e) => f.onChange(e.target.value)}>
              <option value="">{f.rotuloTodos}</option>
              {f.opcoes.map((o) => (
                <option key={o.valor} value={o.valor}>
                  {o.rotulo}{typeof o.contagem === 'number' ? ` (${o.contagem})` : ''}
                </option>
              ))}
            </Select>
          </label>
        ))}
        {ordem ? (
          <label className={styles.lista}>
            <span className="sr-only">Ordenar {nome}</span>
            <Select small value={ordem.valor} aria-label={`Ordenar ${nome}`} onChange={(e) => ordem.onChange(e.target.value)}>
              {ordem.opcoes.map((o) => <option key={o.valor} value={o.valor}>{o.rotulo}</option>)}
            </Select>
          </label>
        ) : null}
        {visao ? (
          <div className={styles.visao} role="group" aria-label="Forma de ver a lista">
            <button type="button" className={styles.visaoBotao} aria-pressed={visao.valor === 'cards'}
                    onClick={() => visao.onChange('cards')}>
              <LayoutGrid size={15} aria-hidden /> <span>Cartões</span>
            </button>
            <button type="button" className={styles.visaoBotao} aria-pressed={visao.valor === 'tabela'}
                    onClick={() => visao.onChange('tabela')}>
              <Rows3 size={15} aria-hidden /> <span>Tabela</span>
            </button>
          </div>
        ) : null}
      </div>
      {chips.map((f) => (
        <div key={f.chave} className={styles.chips} role="group" aria-label={f.rotulo}>
          {[{ valor: '', rotulo: f.rotuloTodos, contagem: f.contagemTodos } as OpcaoListagem, ...f.opcoes].map((o) => {
            const chip = (
              <button key={o.valor || '_todos'} type="button" className={styles.chip} aria-pressed={f.valor === o.valor}
                      onClick={() => f.onChange(f.valor === o.valor ? '' : o.valor)}>
                {o.rotulo}
                {typeof o.contagem === 'number' ? <span className={styles.chipContagem}>{o.contagem}</span> : null}
              </button>
            );
            return o.dica ? <Tooltip key={o.valor} content={o.dica}>{chip}</Tooltip> : chip;
          })}
        </div>
      ))}
      {resumo || onLimpar ? (
        <div className={styles.rodape}>
          {resumo ? <span className={styles.resumo} aria-live="polite">{resumo}</span> : null}
          {onLimpar ? (
            <button type="button" className={styles.limpar} onClick={onLimpar}>
              <X size={13} aria-hidden /> Limpar filtros
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
