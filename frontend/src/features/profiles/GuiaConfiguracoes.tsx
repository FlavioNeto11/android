import { Undo2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type {
  Capability, PolicyGroup, PolicyName, PolicyOrigin, ProfilePolicy, ProfilePolicyPatch, SocialInteraction,
} from '../../api/types';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { Field, Select } from '../../components/Field';
import { type LoadError, LoadErrorBanner, LoadErrorState, toLoadError } from '../../lib/loadError';
import { toastError } from '../../store/toasts';
import { Carregando, useVersaoAoVivo } from './detalheComum';
import type { Pessoa } from './pessoa';
import { LimitsEditor, type Origem, PolicyActionsEditor } from './PolicyEditor';
import { baldeDoLimite, contarUsoDeHoje } from './PolicyVisual';
import styles from './Profiles.module.css';

// ---------------------------------------------------------------- configurações
export function AbaConfiguracoes({ profile, onChanged }: { profile: Pessoa; onChanged: () => Promise<void> }) {
  const [politica, setPolitica] = useState<ProfilePolicy | null>(null);
  const [acoes, setAcoes] = useState<Capability[]>([]);
  const [interacoes, setInteracoes] = useState<SocialInteraction[]>([]);
  const [grupos, setGrupos] = useState<PolicyGroup[]>([]);
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [tentativa, setTentativa] = useState(0);
  // Mudou a política deste perfil (aqui, em outra aba ou pelo grupo): recarrega sozinha, como as demais abas.
  const versao = useVersaoAoVivo(profile);

  useEffect(() => {
    let vivo = true;
    setErro(null);
    // O pacote das capacidades vem do REGISTRO de aplicativos (qual app provê a conta deste perfil), e não de
    // um padrão no cliente: com `listCapabilities()` sem argumento, qualquer chamador recebia o catálogo do
    // Instagram como se fosse o do app dele.
    api.listAppCatalog()
      .then(async (apps) => {
        const alvo = apps.find((a) => a.session_provider !== null);
        const [p, c, i, g] = await Promise.all([
          api.getPolicy(profile.id),
          alvo ? api.listCapabilities(alvo.package) : Promise.resolve([] as Capability[]),
          // O medidor precisa do dia inteiro, não só das últimas dezenas — 200 é folga sobre qualquer teto
          // razoável de "por hora" somado ao longo de um dia. Sem interações não há medidor, não tela quebrada.
          api.listInteractions(profile.id, 200).catch(() => [] as SocialInteraction[]),
          api.listPolicyGroups().catch(() => [] as PolicyGroup[]),
        ]);
        if (!vivo) return;
        setPolitica(p);
        setAcoes(c);
        setInteracoes(i);
        setGrupos(g);
      })
      // Sem política carregada, o erro ocupa a aba com "Tentar de novo"; com ela, a faixa avisa que pode
      // estar velha. Antes ia para um toast e o esqueleto ficava para sempre.
      .catch((e) => vivo && setErro(toLoadError(e)));
    return () => {
      vivo = false;
    };
  }, [profile.id, versao, tentativa]);

  async function salvar(corpo: ProfilePolicyPatch, erro: string) {
    setSalvando(true);
    try {
      setPolitica(await api.setPolicy(profile.id, corpo));
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
      setPolitica(await api.getPolicy(profile.id));
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
  const limitesProprios = Object.keys(politica.own_limits ?? {});
  const usoDeHoje = contarUsoDeHoje(interacoes);

  return (
    <div className={styles.configStack}>
      {erro ? <LoadErrorBanner error={erro} onRetry={() => setTentativa((t) => t + 1)} /> : null}
      <Card>
        <CardHeader title="Grupo de acesso"
                    subtitle="O perfil herda as políticas e os limites do grupo. O que você mudar aqui é deste perfil e sobrepõe o grupo." />
        <CardBody>
          <div className={styles.groupPicker}>
            <Field label="Grupo">
              {({ id }) => (
                <Select id={id} value={politica.group_id ?? ''} disabled={salvando}
                        onChange={(e) => void trocarGrupo(e.target.value)}>
                  <option value="">Sem grupo — só o padrão do catálogo</option>
                  {grupos.map((g) => (
                    <option key={g.id} value={g.id}>{g.name} · {g.members.length} perfil(is)</option>
                  ))}
                </Select>
              )}
            </Field>
            <p className={styles.detail}>
              {proprias.length + limitesProprios.length === 0
                ? 'Nenhuma escolha própria: tudo vem do grupo ou do padrão.'
                : `${proprias.length} ação(ões) e ${limitesProprios.length} limite(s) escolhidos neste perfil${grupoNome ? ' — sobrepõem o grupo' : ''}.`}
            </p>
            {proprias.length + limitesProprios.length ? (
              <Button size="sm" variant="ghost" icon={Undo2} disabled={salvando}
                      onClick={() => void salvar({
                        capabilities: Object.fromEntries(proprias.map((k) => [k, null])),
                        limits: Object.fromEntries(limitesProprios.map((k) => [k, null])),
                      }, 'Não foi possível devolver ao grupo')}>
                {grupoNome ? 'Herdar tudo do grupo' : 'Voltar tudo ao padrão'}
              </Button>
            ) : null}
          </div>
        </CardBody>
      </Card>
      <div className={styles.personaLayout}>
        <Card>
          <CardHeader title="O que este perfil pode fazer"
                      subtitle="Cada ação mostra de onde vem o valor: próprio, do grupo ou padrão. “Herdar” apaga a escolha deste perfil." />
          <CardBody>
            <PolicyActionsEditor
              acoes={acoes} efetivo={efetivo} origem={origem} loosened={politica.loosened ?? []} salvando={salvando}
              herdaria={(c) => doGrupo[c.key] ?? c.default_policy}
              nomeDaHeranca={(c) => (doGrupo[c.key] ? `do grupo ${grupoNome ?? ''}` : 'do padrão do catálogo')}
              onChange={(keys, valor) => void salvar(
                { capabilities: Object.fromEntries(keys.map((k) => [k, valor])) },
                keys.length > 1 ? 'Não foi possível salvar as políticas da categoria' : 'Não foi possível salvar a política')} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Limites"
                      subtitle="Existem para o sistema não agir como robô e derrubar a própria conta." />
          <CardBody className={styles.limitGrid}>
            <LimitsEditor
              limites={politica.limits} salvando={salvando}
              origem={(k) => {
                const o = politica.limits_origin?.[k] ?? 'default';
                if (o === 'own') return { propria: true, rotulo: 'próprio', tone: 'accent' };
                if (o === 'group') return { propria: false, rotulo: `do grupo ${grupoNome ?? ''}`.trim(), tone: 'info' };
                return { propria: false, rotulo: 'padrão', tone: 'muted' };
              }}
              herdaria={(k) => politica.group_limits?.[k]}
              uso={(k) => {
                const balde = baldeDoLimite(k);
                return balde ? usoDeHoje[balde] ?? 0 : null;
              }}
              onChange={(k, valor) => void salvar({ limits: { [k]: valor } }, 'Não foi possível salvar o limite')} />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
