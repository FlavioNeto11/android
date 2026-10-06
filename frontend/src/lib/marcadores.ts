/**
 * 31.134: a gravação e a proposta guardam o dado da persona como marcador (`{perfil_nome}`), nunca o valor. Na tela de uma
 * pessoa, o marcador cru parece defeito; aqui ele vira "[nome da persona]". Os rótulos são os de `available_data.py`
 * (`PROFILE_FIELDS`); marcador que o painel não conhece fica como veio (os parâmetros do comando, `{item}`, não passam por aqui).
 */
const DADO_DA_PERSONA: Readonly<Record<string, string>> = {
  perfil_nome: 'nome', perfil_sobrenome: 'sobrenome', perfil_nome_exibicao: 'nome de exibição', perfil_nascimento: 'data de nascimento',
  perfil_email: 'e-mail', perfil_genero: 'gênero', perfil_idioma: 'idioma e região',
};

const MARCADOR = /\{(perfil_[a-z_]+)\}/g;

/** `true` se o texto tem um marcador de dado da persona que o painel sabe nomear. */
export const temMarcadorDaPersona = (texto: string): boolean => Array.from(texto.matchAll(MARCADOR)).some((m) => m[1]! in DADO_DA_PERSONA);

/** O texto com cada marcador conhecido em palavras ("[nome da persona]"); o resto fica como veio. */
export function textoComMarcadores(texto: string): string {
  return texto.replace(MARCADOR, (inteiro, chave: string) => (chave in DADO_DA_PERSONA ? `[${DADO_DA_PERSONA[chave]} da persona]` : inteiro));
}
