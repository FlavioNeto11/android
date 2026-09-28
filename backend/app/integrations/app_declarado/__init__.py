"""App declarado: os motores genéricos que operam um app a partir do CONHECIMENTO dele, em dado (ADR-052).

O conhecimento de um app mora em `app/conhecimento/apps/<pacote>/` — `telas.yaml` (sinais, regras de tela, extrações,
estado conhecido; fatia 1) e `sessao.yaml` (login e conferência da conta; fatia 3). O código daqui não conhece app
nenhum: nenhum pacote, rótulo, id ou texto de app é escrito em Python. Um app novo com conta gerenciada ganha
login, dispensa de telas benignas, conferência da conta e os mesmos tetos de segurança escrevendo só dado.

- `conhecimento`: carrega e valida o `sessao.yaml` junto do `telas.yaml` do mesmo pacote;
- `formulario`: a geometria do formulário de login e a dispensa de telas intermediárias (heurística do motor);
- `sessao`: `SessaoDeclarada`, o provedor de sessão (`SessionProvider`) dirigido por esse dado.
"""
