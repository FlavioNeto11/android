/**
 * 31.198: os avisos da prévia e do salvar que falam de UMA etapa (31.182: "A etapa “abrir_perfil” mira a conta da própria persona…")
 * aparecem junto dessa etapa, e só os que não apontam para etapa nenhuma ficam na lista geral. O aviso nunca bloqueia: é o texto do
 * central, como veio. A etapa é achada pela chave entre aspas depois de "A etapa", ou pela posição ("etapa 2"); o que não casa com
 * nenhuma etapa da proposta (outro formato, etapa que sumiu) continua na lista geral, nunca se perde.
 */
const COM_CHAVE = /\bA etapa [“"]([^”"]+)[”"]/;
const COM_POSICAO = /\b(?:A )?etapa (\d{1,3})\b/i;

export interface AvisosPorEtapa {
  /** Por chave da etapa, na ordem em que o central mandou. */
  porEtapa: Map<string, string[]>;
  soltos: string[];
}

export function avisosPorEtapa(avisos: readonly string[], etapas: readonly { key: string }[]): AvisosPorEtapa {
  const porEtapa = new Map<string, string[]>();
  const soltos: string[] = [];
  for (const w of avisos) {
    const citada = COM_CHAVE.exec(w)?.[1];
    const posicao = COM_POSICAO.exec(w)?.[1];
    const dona = citada !== undefined && etapas.some((e) => e.key === citada) ? citada : posicao !== undefined ? etapas[Number(posicao) - 1]?.key : undefined;
    if (dona === undefined) soltos.push(w);
    else porEtapa.set(dona, [...(porEtapa.get(dona) ?? []), w]);
  }
  return { porEtapa, soltos };
}
