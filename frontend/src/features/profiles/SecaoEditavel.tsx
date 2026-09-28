/**
 * Uma seção da persona editada e salva SOZINHA: um PATCH só com os campos dela (`biography: { home: {...} }`,
 * `visual: {...}`), que o servidor mescla chave a chave sobre o gravado. Salvar "Trabalho" nunca reescreve "Vida"
 * — era o risco de um formulário único que mandava o JSON inteiro.
 */
import { Save } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { Field, TextArea, TextInput } from '../../components/Field';
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

export function SecaoEditavel({ titulo, subtitulo, marca, campos, iniciais, invalido, onSalvar }: {
  titulo: string;
  subtitulo?: ReactNode;
  /** Selo ao lado do título (ex.: "guardadas, não vão ao modelo"). */
  marca?: ReactNode;
  campos: CampoDef[];
  iniciais: Record<string, string>;
  /** Motivo de não poder salvar ainda (ex.: data fora do formato), ou `null`. */
  invalido?: (valores: Record<string, string>) => string | null;
  onSalvar: (valores: Record<string, string>) => Promise<void>;
}) {
  const [valores, setValores] = useState<Record<string, string>>(iniciais);
  const [salvando, setSalvando] = useState(false);
  const assinatura = JSON.stringify(iniciais);

  // O gravado mudou (salvou aqui, ou outra guia/tela mudou a persona): o formulário volta a espelhá-lo.
  useEffect(() => {
    setValores(JSON.parse(assinatura) as Record<string, string>);
  }, [assinatura]);

  const mudou = campos.some((c) => (valores[c.chave] ?? '') !== (iniciais[c.chave] ?? ''));
  const motivo = !mudou ? 'Nada mudou nesta seção.' : invalido?.(valores) ?? null;

  async function salvar() {
    if (motivo || salvando) return;
    setSalvando(true);
    try {
      await onSalvar(valores);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <Card>
      <CardHeader level={3} title={<span className={styles.secaoTitulo}>{titulo}{marca}</span>} subtitle={subtitulo}
                  actions={
                    <Button size="sm" icon={Save} loading={salvando} disabledReason={motivo} onClick={() => void salvar()}>
                      Salvar {titulo.toLowerCase()}
                    </Button>
                  } />
      <CardBody className={styles.secaoCampos}>
        {campos.map((c) => (
          <Field key={c.chave} label={c.rotulo} unit={c.unidade} hint={c.dica}
                 className={c.tipo === 'lista' || c.tipo === 'longo' ? styles.secaoCampoLargo : undefined}>
            {({ id, describedBy }) => (c.tipo === 'lista' || c.tipo === 'longo' ? (
              <TextArea id={id} aria-describedby={describedBy} rows={c.tipo === 'lista' ? 3 : 2}
                        value={valores[c.chave] ?? ''}
                        onChange={(e) => setValores((v) => ({ ...v, [c.chave]: e.target.value }))} />
            ) : (
              <TextInput id={id} aria-describedby={describedBy} inputMode={c.tipo === 'numero' ? 'numeric' : undefined}
                         value={valores[c.chave] ?? ''}
                         onChange={(e) => setValores((v) => ({ ...v, [c.chave]: e.target.value }))} />
            ))}
          </Field>
        ))}
      </CardBody>
    </Card>
  );
}
