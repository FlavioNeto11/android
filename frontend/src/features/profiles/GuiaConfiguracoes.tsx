import { Undo2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type {
  AppCatalogEntry, Capability, PolicyGroup, PolicyName, PolicyOrigin, ProfilePolicy, ProfilePolicyPatch,
} from '../../api/types';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { Field, Select } from '../../components/Field';
import { plural } from '../../lib/format';
import { type LoadError, LoadErrorBanner, LoadErrorState, toLoadError } from '../../lib/loadError';
import { toastError } from '../../store/toasts';
import { Carregando, useVersaoAoVivo } from './detalheComum';
import type { Pessoa } from './pessoa';
import { type Origem, PolicyActionsEditor } from './PolicyEditor';
import styles from './Profiles.module.css';

/** Apps com catálogo de ações, o âncora primeiro (23.10). Antes o efeito pegava "o primeiro app com login
 *  gerenciado NA ORDEM DA LISTA" — só acertava por acidente, enquanto o Instagram era o único; com um segundo
 *  app de catálogo, a escolha vira deliberada: o âncora por padrão, ou a pessoa escolhe no seletor. */
function appsComCatalogo(catalogo: readonly AppCatalogEntry[]): AppCatalogEntry[] {
  return catalogo.filter((a) => a.has_catalog)
    .slice()
    .sort((a, b) => Number(b.profile_anchor) - Number(a.profile_anchor) || a.label.localeCompare(b.label));
}

// ---------------------------------------------------------------- configurações
export function AbaConfiguracoes({ profile, onChanged }: { profile: Pessoa; onChanged: () => Promise<void> }) {
  const [politica, setPolitica] = useState<ProfilePolicy | null>(null);
  const [acoes, setAcoes] = useState<Capability[]>([]);
  const [grupos, setGrupos] = useState<PolicyGroup[]>([]);
  // `null` = o catálogo ainda não chegou (ou falhou: aí o erro está em `erro`); `[]` = chegou, sem app nenhum.
  const [catalogo, setCatalogo] = useState<AppCatalogEntry[] | null>(null);
  // `null` = o app âncora (ou o único com catálogo); a pessoa escolhe outro quando há mais de um (23.10).
  const [pacoteEscolhido, setPacoteEscolhido] = useState<string | null>(null);
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [tentativa, setTentativa] = useState(0);
  // Mudou a política deste perfil (aqui, em outra aba ou pelo grupo): recarrega sozinha, como as demais abas.
  const versao = useVersaoAoVivo(profile);

  // O REGISTRO de aplicativos diz quem tem catálogo e quem é o âncora; não é um padrão fixo no cliente. A falha
  // dele é erro da aba, com "Tentar de novo" (que o busca de novo, por `tentativa`): engolida, o app nunca se
  // resolvia e o esqueleto ficava para sempre.
  useEffect(() => {
    let vivo = true;
    api.listAppCatalog()
      .then((c) => { if (vivo) setCatalogo(c); })
      .catch((e) => { if (vivo) setErro(toLoadError(e)); });
    return () => { vivo = false; };
  }, [tentativa]);

  const apps = appsComCatalogo(catalogo ?? []);
  // Nenhum app com catálogo: `null`, e a política vem sem `?package=` (o servidor resolve o âncora) — a aba ainda
  // mostra o grupo, só sem a lista de ações.
  const pacoteEfetivo = pacoteEscolhido ?? apps[0]?.package ?? null;

  useEffect(() => {
    let vivo = true;
    if (catalogo === null) return undefined;            // aguarda o catálogo (efeito acima) resolver o app
    setErro(null);
    Promise.all([
      api.getPolicy(profile.id, pacoteEfetivo),
      // `listCapabilities` exige o pacote: sem app com catálogo não há ações a listar, e não se pergunta.
      pacoteEfetivo ? api.listCapabilities(pacoteEfetivo) : Promise.resolve([] as Capability[]),
      api.listPolicyGroups(pacoteEfetivo).catch(() => [] as PolicyGroup[]),
    ])
      .then(([p, c, g]) => {
        if (!vivo) return;
        setPolitica(p);
        setAcoes(c);
        setGrupos(g);
      })
      // Sem política carregada, o erro ocupa a aba com "Tentar de novo"; com ela, a faixa avisa que pode
      // estar velha. Antes ia para um toast e o esqueleto ficava para sempre.
      .catch((e) => vivo && setErro(toLoadError(e)));
    return () => {
      vivo = false;
    };
  }, [profile.id, versao, tentativa, pacoteEfetivo, catalogo]);

  async function salvar(corpo: ProfilePolicyPatch, erro: string) {
    setSalvando(true);
    try {
      setPolitica(await api.setPolicy(profile.id, corpo, pacoteEfetivo));
    } catch (e) {
      toastError(erro, e);
    } finally {
      setSalvando(false);
    }
  }

  async function trocarGrupo(groupId: string) {
    setSalvando(true);
    try {
      await api.patchProfile(profile.id, { policy_group_id: groupId || null });
      setPolitica(await api.getPolicy(profile.id, pacoteEfetivo));
      await onChanged();
    } catch (e) {
      toastError('Não foi possível trocar o grupo de acesso', e);
    } finally {
      setSalvando(false);
    }
  }

  if (!politica) {
    return erro
      ? <LoadErrorState what="as políticas" error={erro} onRetry={() => setTentativa((t) => t + 1)} />
      : <Carregando />;
  }

  const grupoNome = politica.group_name ?? null;
  const doGrupo = politica.group ?? {};
  const efetivo = (c: Capability): PolicyName => politica.capabilities[c.key] ?? c.default_policy;
  const origemDe = (c: Capability): PolicyOrigin =>
    politica.origin?.[c.key] ?? (efetivo(c) !== c.default_policy ? 'own' : 'default');
  const origem = (c: Capability): Origem => {
    const o = origemDe(c);
    if (o === 'own') {
      const sobrepoe = grupoNome && doGrupo[c.key] && doGrupo[c.key] !== efetivo(c);
      return { propria: true, rotulo: sobrepoe ? 'próprio · sobrepõe o grupo' : 'próprio', tone: 'accent' };
    }
    if (o === 'group') return { propria: false, rotulo: `do grupo ${grupoNome ?? ''}`.trim(), tone: 'info' };
    return { propria: false, rotulo: 'padrão', tone: 'muted' };
  };
  const proprias = acoes.filter((c) => origemDe(c) === 'own').map((c) => c.key);

  return (
    <div className={styles.configStack}>
      {erro ? <LoadErrorBanner error={erro} onRetry={() => setTentativa((t) => t + 1)} /> : null}
      <Card>
        <CardHeader title="Grupo de acesso"
                    subtitle="A persona herda as políticas do grupo. O que você mudar aqui é desta persona e sobrepõe o grupo." />
        <CardBody>
          <div className={styles.groupPicker}>
            <Field label="Grupo">
              {({ id }) => (
                <Select id={id} value={politica.group_id ?? ''} disabled={salvando}
                        onChange={(e) => void trocarGrupo(e.target.value)}>
                  <option value="">Sem grupo — só o padrão do catálogo</option>
                  {grupos.map((g) => (
                    <option key={g.id} value={g.id}>{g.name} · {plural(g.members.length, 'persona', 'personas')}</option>
                  ))}
                </Select>
              )}
            </Field>
            <p className={styles.detail}>
              {proprias.length === 0
                ? 'Nenhuma escolha própria: tudo vem do grupo ou do padrão.'
                : `${plural(proprias.length, 'ação escolhida', 'ações escolhidas')} nesta persona${grupoNome ? ' — sobrepõem o grupo' : ''}.`}
            </p>
            {proprias.length ? (
              <Button size="sm" variant="ghost" icon={Undo2} disabled={salvando}
                      onClick={() => void salvar({
                        capabilities: Object.fromEntries(proprias.map((k) => [k, null])),
                      }, 'Não foi possível devolver ao grupo')}>
                {grupoNome ? 'Herdar tudo do grupo' : 'Voltar tudo ao padrão'}
              </Button>
            ) : null}
          </div>
        </CardBody>
      </Card>
      <div className={styles.personaLayout}>
        <Card>
          <CardHeader title="O que esta persona pode fazer"
                      subtitle="Cada ação mostra de onde vem o valor: próprio, do grupo ou padrão. “Herdar” apaga a escolha desta persona." />
          <CardBody>
            {apps.length > 1 ? (
              <Field label="Aplicativo" hint="Cada app tem o catálogo e a política dele; a pessoa escolhe qual está vendo.">
                {({ id, describedBy }) => (
                  <Select id={id} aria-describedby={describedBy} value={pacoteEfetivo ?? ''} disabled={salvando}
                          onChange={(e) => setPacoteEscolhido(e.target.value)}>
                    {apps.map((a) => <option key={a.package} value={a.package}>{a.label}</option>)}
                  </Select>
                )}
              </Field>
            ) : null}
            <PolicyActionsEditor
              acoes={acoes} efetivo={efetivo} origem={origem} loosened={politica.loosened ?? []} salvando={salvando}
              herdaria={(c) => doGrupo[c.key] ?? c.default_policy}
              nomeDaHeranca={(c) => (doGrupo[c.key] ? `do grupo ${grupoNome ?? ''}` : 'do padrão do catálogo')}
              onChange={(keys, valor) => void salvar(
                { capabilities: Object.fromEntries(keys.map((k) => [k, valor])) },
                keys.length > 1 ? 'Não foi possível salvar as políticas da categoria' : 'Não foi possível salvar a política')} />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
