import { ShieldAlert, SlidersHorizontal, Smartphone } from 'lucide-react';
import { useState } from 'react';
import { api, toApiError } from '../../api/client';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Checkbox, Field, Select, TextInput } from '../../components/Field';
import { uuid } from '../../lib/ids';
import { useAppStore } from '../../store/app';
import { toast } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import {
  RAM_MAX_MB, RAM_MIN_MB, montarPedido, recusaDoProvisionamento, type ErrosDoRascunho, type RascunhoDeAparelho,
  type RecusaNaTela,
} from './provisionamento';
import styles from './Infra.module.css';

const VAZIO: RascunhoDeAparelho = { appId: '', imagem: '', ramMb: '', ligar: false };

/**
 * "Criar aparelho" neste servidor (adendo v0.26, ADR-045): a instância nasce `dynamic` e o AVD vem pelo comando
 * `create` de sempre — cerca, outbox, desfecho em `GET /api/commands/{id}`. A recusa (teto, disco, conflito) fica
 * NA tela, com o número que o servidor viu; um toast sumiria antes de a pessoa ler o que fazer.
 */
export function CriarAparelhoDialog({ onClose }: { onClose: () => void }) {
  const apps = useAppStore((s) => s.apps);
  const imagemPadrao = useAppStore((s) => s.health?.features?.system_image ?? null);
  const upsertInstance = useAppStore((s) => s.upsertInstance);
  const navegar = useUiStore((s) => s.navegar);
  const [rascunho, setRascunho] = useState<RascunhoDeAparelho>(VAZIO);
  const [erros, setErros] = useState<ErrosDoRascunho>({});
  const [recusa, setRecusa] = useState<RecusaNaTela | null>(null);
  const [enviando, setEnviando] = useState(false);
  // UMA chave por abertura do diálogo: um 202 perdido na rede e reenviado devolve o MESMO aparelho em vez de criar
  // outro. Só muda quando o backend diz que ela já serviu a um aparelho aposentado (`chave_ja_usada`).
  const [chave, setChave] = useState(uuid);

  const mudar = <K extends keyof RascunhoDeAparelho>(k: K, v: RascunhoDeAparelho[K]) => setRascunho((r) => ({ ...r, [k]: v }));

  const criar = async () => {
    const { erros: achados, corpo } = montarPedido(rascunho, chave);
    setErros(achados);
    if (!corpo) return;
    setEnviando(true);
    setRecusa(null);
    try {
      const r = await api.provisionInstance(corpo);
      upsertInstance(r.instance);
      toast({
        tone: 'success',
        title: r.deduplicated ? `${r.instance_id} já tinha sido pedido` : `Aparelho ${r.instance_id} criado`,
        message: r.start === 'after_create'
          ? 'O AVD está sendo criado; o aparelho liga quando a criação terminar.'
          : 'O AVD está sendo criado; ligue o aparelho quando quiser.',
        hint: r.command_id ? `Comando ${r.command_id}` : null,
      });
      onClose();
    } catch (e) {
      const err = toApiError(e);
      if (err.code === 'chave_ja_usada') setChave(uuid());
      setRecusa(recusaDoProvisionamento(err));
    } finally {
      setEnviando(false);
    }
  };

  const irParaLimites = () => {
    // A guia de Configuração vem do link; abrir já em Limites poupa a pessoa de procurar.
    onClose();
    navegar({ tela: 'configuracao', query: { aba: 'limites' } });
  };

  return (
    <Dialog
      open
      onClose={onClose}
      size="md"
      icon={Smartphone}
      title="Criar aparelho neste servidor"
      footer={(
        <>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button variant="primary" icon={Smartphone} loading={enviando} onClick={() => void criar()}>Criar</Button>
        </>
      )}
    >
      <form
        className={styles.formulario}
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          void criar();
        }}
      >
        <p className={styles.dim}>
          Um aparelho novo nesta máquina: a instância entra no parque agora e o AVD é criado por um comando, que
          aparece em Execuções como qualquer outro. Para tirá-lo depois, desligue e use “Aposentar” na lista.
        </p>
        {recusa ? (
          <Banner
            tone="danger"
            icon={ShieldAlert}
            compact
            role="alert"
            title={recusa.titulo}
            actions={recusa.irParaLimites
              ? <Button size="sm" variant="outline" icon={SlidersHorizontal} onClick={irParaLimites}>Abrir Configuração → Limites</Button>
              : undefined}
          >
            {recusa.passo ? <p>{recusa.passo}</p> : null}
            <p className={styles.dim}>{recusa.mensagem}</p>
          </Banner>
        ) : null}
        <Field label="Aplicativo" hint="O app que este aparelho vai operar. Pode ficar para depois (Configuração → Aparelhos e contas).">
          {(f) => (
            <Select id={f.id} aria-describedby={f.describedBy} value={rascunho.appId} onChange={(e) => mudar('appId', e.target.value)}>
              <option value="">Nenhum por enquanto</option>
              {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          )}
        </Field>
        <Field
          label="Imagem do sistema"
          unit="opcional"
          error={erros.imagem}
          hint={`Formato do SDK. Vazio = a imagem padrão deste servidor${imagemPadrao ? ` (${imagemPadrao})` : ''}.`}
        >
          {(f) => (
            <TextInput
              id={f.id}
              aria-describedby={f.describedBy}
              invalid={f.invalid}
              mono
              value={rascunho.imagem}
              placeholder="system-images;android-34;google_apis_playstore;x86_64"
              onChange={(e) => mudar('imagem', e.target.value)}
            />
          )}
        </Field>
        <Field
          label="RAM"
          unit="MB, opcional"
          error={erros.ramMb}
          hint={`Entre ${RAM_MIN_MB.toLocaleString('pt-BR')} e ${RAM_MAX_MB.toLocaleString('pt-BR')} MB. Vazio = o padrão da configuração.`}
        >
          {(f) => (
            <TextInput
              id={f.id}
              aria-describedby={f.describedBy}
              invalid={f.invalid}
              inputMode="numeric"
              value={rascunho.ramMb}
              placeholder="2048"
              onChange={(e) => mudar('ramMb', e.target.value)}
            />
          )}
        </Field>
        <Checkbox label="Ligar depois de criar" checked={rascunho.ligar} onChange={(e) => mudar('ligar', e.target.checked)} />
        {/* Enter num campo cria, como o botão do rodapé. */}
        <button type="submit" hidden aria-hidden tabIndex={-1} />
      </form>
    </Dialog>
  );
}
