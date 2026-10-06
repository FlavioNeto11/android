-- 126_troca_de_conta — item 31.155, ADR-080 (número reservado pela orquestradora em 06/10).
--
-- Duas personas do mesmo app no mesmo aparelho passam a conviver quando o app DECLARA a troca de conta (`troca` no
-- `sessao.yaml`): o motor de sessão tira a conta aberta e entra na esperada. O índice único da 051
-- (`ux_binding_conta_do_app_no_aparelho`, uma conta por app em cada aparelho) não lê o `sessao.yaml`, então sai; a regra
-- D2-a fica no repositório (`SocialRepository.quem_ja_serve`), que já era mais estrita que o índice (enxerga o vínculo
-- sem app de quem tem conta no app) e agora a relaxa só para o app que declara a troca. Nenhuma linha muda.
DROP INDEX IF EXISTS ux_binding_conta_do_app_no_aparelho;
