import type { InstagramProfile, PersonaDevice, PersonaDTO, PersonaOnDevice } from '../../api/types';

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
 * backend (`models.py`). As crenças também vão, mas como seção própria (`CrencasPersona.tsx`, ADR-048), não como
 * linha curta. O resto da biografia fica guardado e não sai daqui — a tela marca a diferença.
 */
export const PERSONA_BIO_FIELDS: ReadonlySet<string> = new Set([
  // Desde 28/09 (decisão do dono), a biografia inteira vai: tudo o que a pessoa é influencia a fala, com orçamento.
  'home.city', 'work.profession', 'work.education', 'tastes.hobbies', 'origin.birthplace', 'origin.hometown',
  'home.residence', 'life.marital_status', 'life.children', 'work.employer', 'tastes.preferences',
  'tastes.dislikes', 'life.history', 'home.state', 'home.country', 'origin.nationality',
]);

/**
 * Os aparelhos da persona (v0.29, N:N), o principal primeiro. Backend anterior ao N:N (sem `devices`): o
 * `instance_id`, que era o único vínculo, com a sessão do perfil — a tela não some com os dados de quem ainda não
 * atualizou.
 */
export function aparelhosDe(p: Pick<Pessoa, 'instance_id' | 'session' | 'devices'>): PersonaDevice[] {
  if (p.devices) return p.devices;
  if (!p.instance_id) return [];
  return [{ instance_id: p.instance_id, app_id: null, is_primary: true, state: null, worker_id: null, bound_at: null,
            session: p.session ?? null }];
}

/** Os ids dos aparelhos da persona, sem repetir (um aparelho pode ter um vínculo por app), o principal primeiro. */
export function idsDosAparelhos(p: Pick<Pessoa, 'instance_id' | 'session' | 'devices'>): string[] {
  return [...new Set(aparelhosDe(p).map((d) => d.instance_id))];
}

/**
 * Aparelho → personas, invertendo os `devices[]` de cada persona: a mesma forma de `GET /instances/{id}/personas`,
 * sem uma chamada por aparelho. É o que a grade e a Infraestrutura usam para mostrar as N personas de cada aparelho
 * com uma leitura só da lista de personas.
 */
export function personasPorAparelho(pessoas: readonly Pessoa[]): Map<string, PersonaOnDevice[]> {
  const mapa = new Map<string, PersonaOnDevice[]>();
  for (const p of pessoas) {
    for (const d of aparelhosDe(p)) {
      const lista = mapa.get(d.instance_id) ?? [];
      lista.push({
        profile_id: p.id, username: handleDe(p), display_name: p.display_name, name: nomeDe(p), status: p.status,
        app_id: d.app_id, is_primary: d.is_primary, bound_at: d.bound_at, session: d.session,
      });
      mapa.set(d.instance_id, lista);
    }
  }
  return mapa;
}
