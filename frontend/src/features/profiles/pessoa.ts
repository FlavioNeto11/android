import type { InstagramProfile, PersonaDTO } from '../../api/types';

/**
 * A persona é a pessoa (ADR-041): o mesmo objeto chega como `PersonaDTO` (`GET /personas`, `username` nulo quando a
 * pessoa ainda não tem conta de cadastro) ou como `InstagramProfile` (o nome antigo, que as telas e os testes de
 * perfil ainda usam). As guias aceitam os dois e só leem o que é comum.
 */
export type Pessoa = InstagramProfile | PersonaDTO;

/** O @ de cadastro, ou `null` para quem ainda não tem conta (a coluna guarda `''`; a API traduz para nulo). */
export function handleDe(p: Pick<Pessoa, 'username'>): string | null {
  return p.username ? p.username : null;
}

/** Nome para mostrar: o nome da persona; senão o de exibição; senão nome e sobrenome; senão o @. Nunca vazio. */
export function nomeDe(p: Pessoa): string {
  const nome = ('name' in p && typeof p.name === 'string' ? p.name : '').trim();
  if (nome) return nome;
  if (p.display_name?.trim()) return p.display_name.trim();
  const completo = [p.first_name, p.last_name].filter(Boolean).join(' ').trim();
  if (completo) return completo;
  const h = handleDe(p);
  return h ? `@${h}` : 'Pessoa sem nome';
}

/** Idade · cidade · profissão, só o que existe. É o que diferencia uma pessoa da outra num cartão. */
export function resumoDe(p: Pessoa): string[] {
  const partes: string[] = [];
  if (typeof p.age === 'number') partes.push(`${p.age} anos`);
  const cidade = p.biography?.home?.city;
  if (cidade) partes.push(cidade);
  const profissao = p.biography?.work?.profession;
  if (profissao) partes.push(profissao);
  return partes;
}

/**
 * O que da BIOGRAFIA vai ao modelo, como linha curta no bloco `<persona>`: espelho de `PERSONA_BIO_FIELDS` do
 * backend (`models.py`). O resto da biografia fica guardado e não sai daqui — a tela marca a diferença.
 */
export const PERSONA_BIO_FIELDS: ReadonlySet<string> = new Set([
  'home.city', 'work.profession', 'work.education', 'tastes.hobbies',
]);
