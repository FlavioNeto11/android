import { CircleAlert, LogIn } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Field, TextInput } from '../../components/Field';
import { useSessionStore } from '../../store/session';

/**
 * A tela que faltava. Duas coisas acontecem aqui, e só aqui:
 *
 * 1. **A pessoa diz o nome.** Não é um usuário do sistema operacional nem uma conta com senha própria (isso é
 *    decisão de quem cuida do parque, não do painel): é o nome que vai ficar gravado em cada comando,
 *    aprovação e resolução à mão. Antes disto, tudo dizia `panel`.
 * 2. **De fora do loopback, a chave de acesso vira cookie.** O `API_TOKEN` não tinha como viajar num
 *    `new WebSocket(...)` nem num `<img src=...>`; o cookie tem. É o que permite abrir o painel de outra
 *    estação — com frame, evidência e avatar carregando.
 */
export function LoginPage() {
  const { tokenRequired, busy, error, lastName, signIn } = useSessionStore();
  const [nome, setNome] = useState(lastName);
  const [chave, setChave] = useState('');
  const [tocado, setTocado] = useState(false);

  const nomeInvalido = tocado && nome.trim().length < 2;

  async function enviar(e: FormEvent): Promise<void> {
    e.preventDefault();
    setTocado(true);
    if (nome.trim().length < 2) return;
    const ok = await signIn(nome, chave);
    if (ok) setChave('');           // a chave não fica na memória da página depois de virar cookie
  }

  return (
    <main style={{ maxWidth: 460, margin: '10vh auto', padding: '0 16px' }}>
      <h1 style={{ fontSize: 22, marginBottom: 4 }}>Central de Aparelhos</h1>
      <p style={{ marginTop: 0, marginBottom: 20, opacity: 0.75 }}>
        Diga quem está operando: cada ação no parque fica gravada com este nome.
      </p>

      {error ? <Banner tone="danger" icon={CircleAlert} role="alert" title="Não foi possível entrar">{error}</Banner> : null}

      <form onSubmit={(e) => void enviar(e)} style={{ display: 'grid', gap: 14, marginTop: 14 }}>
        <Field
          label="Seu nome"
          error={nomeInvalido ? 'Diga um nome com pelo menos 2 caracteres.' : null}
          hint="É o que vai aparecer na auditoria de cada comando."
        >
          {(f) => (
            <TextInput
              id={f.id}
              aria-describedby={f.describedBy}
              invalid={f.invalid}
              value={nome}
              autoFocus
              autoComplete="username"
              placeholder="Ana Ribeiro"
              onChange={(e) => setNome(e.target.value)}
            />
          )}
        </Field>

        {tokenRequired ? (
          <Field
            label="Chave de acesso"
            hint="É o API_TOKEN configurado no servidor. Peça a quem cuida do parque — o painel não a guarda."
          >
            {(f) => (
              <TextInput
                id={f.id}
                aria-describedby={f.describedBy}
                type="password"
                autoComplete="current-password"
                value={chave}
                onChange={(e) => setChave(e.target.value)}
              />
            )}
          </Field>
        ) : (
          <p style={{ margin: 0, fontSize: 13, opacity: 0.7 }}>
            Este servidor está sendo aberto da própria máquina: não é preciso chave de acesso.
          </p>
        )}

        <Button type="submit" variant="primary" icon={LogIn} loading={busy} block>Entrar</Button>
      </form>
    </main>
  );
}
