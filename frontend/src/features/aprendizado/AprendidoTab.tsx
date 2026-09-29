import { BookOpen, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { formatInt } from '../../lib/format';
import { LoadErrorBanner, LoadErrorState, toLoadError, type LoadError } from '../../lib/loadError';
import { apiAprendizado, type FiltroDoLivro } from './api';
import { ItemDoLivro, chaveDoItem } from './ItemDoLivro';
import {
  ESTADOS_DO_LIVRO, LIVRO_KINDS, ORIGENS, ORIGEM_LABEL, type ListaDoLivro, acoesDoItem, isEstadoDoLivro, isLivroKind,
  rotuloDoEstado, rotuloDoKind,
} from './model';
import styles from './Aprendizado.module.css';

/** Quantos itens por tipo e estado (a memória conta lembranças, não linhas). */
function Contagem({ contagem }: { contagem: NonNullable<ListaDoLivro['contagem']> }) {
  const tipos = Object.entries(contagem).filter(([, porEstado]) => Object.keys(porEstado).length > 0);
  if (tipos.length === 0) return null;
  return (
    <div className={styles.resumo} role="group" aria-label="Contagem por tipo e estado">
      {tipos.map(([kind, porEstado]) => (
        <span key={kind} className={styles.resumoChip}>
          <strong>{rotuloDoKind(kind)}</strong>
          {Object.entries(porEstado).map(([estado, n]) => {
            // Os rótulos de estado fazem o plural com "s" (publicado → publicados, desligado → desligados).
            const rotulo = kind === 'memoria' ? 'lembranças' : estado === '-' ? 'sem estado'
              : `${rotuloDoEstado(isEstadoDoLivro(estado) ? estado : null).toLowerCase()}${n === 1 ? '' : 's'}`;
            return <span key={estado}>{formatInt(n)} {rotulo}</span>;
          })}
        </span>
      ))}
    </div>
  );
}

/**
 * O catálogo unificado (ADR-054, decisão 3): receitas, fluxos, habilidades, memória (só a contagem) e os itens do
 * livro, com o estado, o efeito medido, o último uso e as decisões da pessoa — desligar, aposentar e reativar, sempre
 * com motivo. Habilidade tem ciclo próprio (publicar é sempre de uma pessoa, pela tela dela).
 */
export function AprendidoTab() {
  const [filtro, setFiltro] = useState<FiltroDoLivro>({});
  const [lista, setLista] = useState<ListaDoLivro | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregando, setCarregando] = useState(true);
  const vez = useRef(0);

  const carregar = useCallback(async (f: FiltroDoLivro) => {
    const minha = ++vez.current;
    setCarregando(true);
    try {
      const res = await apiAprendizado.livro(f);
      if (minha !== vez.current) return;
      setLista({ itens: Array.isArray(res?.itens) ? res.itens : [], total: res?.total ?? 0, contagem: res?.contagem });
      setErro(null);
    } catch (e) {
      if (minha === vez.current) setErro(toLoadError(e));
    } finally {
      if (minha === vez.current) setCarregando(false);
    }
  }, []);

  useEffect(() => {
    void carregar(filtro);
  }, [carregar, filtro]);

  return (
    <section className={styles.secao} aria-label="Aprendido">
      <div className={styles.toolbar}>
        <Field label="Tipo" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={filtro.kind ?? ''}
                    onChange={(e) => setFiltro((f) => ({ ...f, kind: isLivroKind(e.target.value) ? e.target.value : undefined }))}>
              <option value="">Todos</option>
              {LIVRO_KINDS.map((k) => <option key={k} value={k}>{rotuloDoKind(k)}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Estado" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={filtro.state ?? ''}
                    onChange={(e) => setFiltro((f) => ({ ...f, state: isEstadoDoLivro(e.target.value) ? e.target.value : undefined }))}>
              <option value="">Todos</option>
              {ESTADOS_DO_LIVRO.map((s) => <option key={s} value={s}>{rotuloDoEstado(s)}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Origem" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={filtro.origem ?? ''}
                    onChange={(e) => setFiltro((f) => ({ ...f, origem: ORIGENS.find((o) => o === e.target.value) }))}>
              <option value="">Todas</option>
              {ORIGENS.map((o) => <option key={o} value={o}>{ORIGEM_LABEL[o]}</option>)}
            </Select>
          )}
        </Field>
        <div className={styles.toolbarFim}>
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando} onClick={() => void carregar(filtro)}>Atualizar</Button>
        </div>
      </div>

      {erro && lista ? <LoadErrorBanner error={erro} onRetry={() => void carregar(filtro)} /> : null}
      {!lista ? (
        erro ? <LoadErrorState what="o livro de aprendizado" error={erro} onRetry={() => void carregar(filtro)} /> : (
          <LoadingRegion label="Carregando o livro de aprendizado…" className={styles.secao}>
            <Skeleton height={32} radius={8} />
            <Skeleton height={72} radius={8} />
          </LoadingRegion>
        )
      ) : (
        <>
          {lista.contagem ? <Contagem contagem={lista.contagem} /> : null}
          {lista.itens.length === 0 ? (
            <EmptyState icon={BookOpen} compact title="Nada aprendido com este filtro" />
          ) : (
            <ul className={styles.lista} aria-label="Catálogo do aprendizado">
              {lista.itens.map((e) => (
                <ItemDoLivro
                  key={chaveDoItem(e)}
                  entrada={e}
                  acoes={acoesDoItem(e)}
                  onMudou={() => void carregar(filtro)}
                  extra={e.kind === 'habilidade'
                    ? <p className={styles.secaoLead}>O ciclo da habilidade (publicar, desligar) fica na aba Habilidades da persona: publicar é sempre de uma pessoa.</p>
                    : null}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
