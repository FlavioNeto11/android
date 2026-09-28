/**
 * Uma seção da persona editada e salva SOZINHA: um PATCH só com os campos dela (`biography: { home: {...} }`,
 * `visual: {...}`), que o servidor mescla chave a chave sobre o gravado. Salvar "Trabalho" nunca reescreve "Vida"
 * — era o risco de um formulário único que mandava o JSON inteiro.
 *
 * Com `leitura`, a seção abre LENDO (o mapa da pessoa: chips, linha do tempo, gosta × não gosta) e o formulário só
 * aparece em "Editar {seção}" — o mesmo gesto das Crenças e da voz. Sem `leitura`, é o formulário de sempre (a guia
 * Imagens usa assim).
 */
import { Pencil, Save, X } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { Field, TextArea, TextInput } from '../../components/Field';
import { cx } from '../../lib/format';
import styles from './Profiles.module.css';

export interface CampoDef {
  chave: string;
  rotulo: string;
  /** `lista`: uma por linha; `numero`: inteiro ou vazio; `longo`: texto de várias linhas. */
  tipo?: 'texto' | 'lista' | 'numero' | 'longo';
  dica?: string;
  /** Observação curta ao lado do rótulo (ex.: "vai ao modelo"). */
  unidade?: string;
}

/** Texto vazio é apagar o campo (`null`, que o servidor entende como "tira a chave"), nunca gravar `''`. */
export function paraTexto(v: string | undefined): string | null {
  return (v ?? '').trim() || null;
}

/** Lista editada como texto: uma por linha. Guardar vazio é apagar a lista, não gravar `['']`. */
export function paraLista(v: string | undefined): string[] {
  return (v ?? '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
}

export function paraNumero(v: string | undefined): number | null {
  const t = (v ?? '').trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? Math.trunc(n) : null;
}

/** O valor gravado como o formulário o mostra. */
export function comoTexto(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (Array.isArray(v)) return v.join('\n');
  return String(v);
}

/** Quanto da seção está preenchido — o ponto colorido do índice e o selo do cartão. */
export type Preenchimento = 'vazia' | 'parcial' | 'completa';

export function preenchimentoDe(campos: CampoDef[], valores: Record<string, string>): Preenchimento {
  const cheios = campos.filter((c) => (valores[c.chave] ?? '').trim() !== '').length;
  return cheios === 0 ? 'vazia' : cheios === campos.length ? 'completa' : 'parcial';
}

const SELO: Record<Preenchimento, { tom: 'success' | 'warning' | 'neutral'; texto: string }> = {
  completa: { tom: 'success', texto: 'completa' },
  parcial: { tom: 'warning', texto: 'parcial' },
  vazia: { tom: 'neutral', texto: 'vazia' },
};

export function SecaoEditavel({ id, titulo, icone, subtitulo, marca, campos, iniciais, invalido, onSalvar, leitura }: {
  /** Âncora da seção (o índice e o retrato rolam até ela). */
  id?: string;
  titulo: string;
  icone?: ReactNode;
  subtitulo?: ReactNode;
  /** Selo ao lado do título (ex.: "guardadas, não vão ao modelo"). */
  marca?: ReactNode;
  campos: CampoDef[];
  iniciais: Record<string, string>;
  /** Motivo de não poder salvar ainda (ex.: data fora do formato), ou `null`. */
  invalido?: (valores: Record<string, string>) => string | null;
  onSalvar: (valores: Record<string, string>) => Promise<void>;
  /** O modo de leitura. Presente: a seção abre lendo e o formulário fica atrás de "Editar {titulo}". */
  leitura?: ReactNode;
}) {
  const [valores, setValores] = useState<Record<string, string>>(iniciais);
  const [salvando, setSalvando] = useState(false);
  const [editando, setEditando] = useState(leitura === undefined);
  const assinatura = JSON.stringify(iniciais);

  // O gravado mudou (salvou aqui, ou outra guia/tela mudou a persona): o formulário volta a espelhá-lo.
  useEffect(() => {
    setValores(JSON.parse(assinatura) as Record<string, string>);
  }, [assinatura]);

  const mudou = campos.some((c) => (valores[c.chave] ?? '') !== (iniciais[c.chave] ?? ''));
  const motivo = !mudou ? 'Nada mudou nesta seção.' : invalido?.(valores) ?? null;
  const comLeitura = leitura !== undefined;
  const estado = preenchimentoDe(campos, iniciais);

  async function salvar() {
    if (motivo || salvando) return;
    setSalvando(true);
    try {
      await onSalvar(valores);
      // Só volta a ler quando o servidor gravou: o `iniciais` novo chega e o efeito acima zera o formulário.
      if (comLeitura) setEditando(false);
    } finally {
      setSalvando(false);
    }
  }

  function cancelar() {
    setValores(JSON.parse(assinatura) as Record<string, string>);
    setEditando(false);
  }

  const acoes = !comLeitura ? (
    <Button size="sm" icon={Save} loading={salvando} disabledReason={motivo} onClick={() => void salvar()}>
      Salvar {titulo.toLowerCase()}
    </Button>
  ) : editando ? (
    <span className={styles.secaoAcoes}>
      <Button size="sm" variant="ghost" icon={X} onClick={cancelar}>Cancelar</Button>
      <Button size="sm" variant="primary" icon={Save} loading={salvando} disabledReason={motivo}
              onClick={() => void salvar()}>
        Salvar {titulo.toLowerCase()}
      </Button>
    </span>
  ) : (
    <Button size="sm" variant="ghost" icon={Pencil} onClick={() => setEditando(true)}>Editar {titulo.toLowerCase()}</Button>
  );

  return (
    <Card id={id} className={cx(comLeitura && styles.secaoMapa)} data-preenchimento={comLeitura ? estado : undefined}>
      <CardHeader level={3}
                  title={(
                    <span className={styles.secaoTitulo}>
                      {icone}{titulo}{marca}
                      {comLeitura ? <Badge size="sm" tone={SELO[estado].tom}>{SELO[estado].texto}</Badge> : null}
                    </span>
                  )}
                  subtitle={subtitulo} actions={acoes} />
      {comLeitura && !editando ? (
        <CardBody className={styles.secaoLeitura}>{leitura}</CardBody>
      ) : (
        <CardBody className={styles.secaoCampos}>
          {campos.map((c) => (
            <Field key={c.chave} label={c.rotulo} unit={c.unidade} hint={c.dica}
                   className={c.tipo === 'lista' || c.tipo === 'longo' ? styles.secaoCampoLargo : undefined}>
              {({ id: campoId, describedBy }) => (c.tipo === 'lista' || c.tipo === 'longo' ? (
                <TextArea id={campoId} aria-describedby={describedBy} rows={c.tipo === 'lista' ? 3 : 2}
                          value={valores[c.chave] ?? ''}
                          onChange={(e) => setValores((v) => ({ ...v, [c.chave]: e.target.value }))} />
              ) : (
                <TextInput id={campoId} aria-describedby={describedBy} inputMode={c.tipo === 'numero' ? 'numeric' : undefined}
                           value={valores[c.chave] ?? ''}
                           onChange={(e) => setValores((v) => ({ ...v, [c.chave]: e.target.value }))} />
              ))}
            </Field>
          ))}
        </CardBody>
      )}
    </Card>
  );
}
