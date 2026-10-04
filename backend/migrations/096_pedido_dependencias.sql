-- Colaboração entre pedidos, a ESTRUTURA (item 28.10, fatia F1, Fase 28; `docs/design/pedidos-persistentes.md` §9).
-- Número 096 reservado pela orquestradora; a lacuna na numeração da main (089 a 095) é esperada até os outros branches
-- entrarem.
--
-- Por quê: um pedido que precisa de mais de uma cabeça (um pesquisador, um checador, um redator, um porta-voz) hoje só
-- pode ser vários pedidos soltos, sem ninguém que diga quem é filho de quem, quem espera o resultado de quem, nem quem
-- é o único que age para fora. A 067 deixou `pedidos.pai_id` de lado ("a 28.10 decide profundidade, ciclo e linhagem");
-- esta migração decide a FORMA. As regras (profundidade, quantidade de filhos, ciclo, linhagem, porta-voz único,
-- orçamento reservado do pai) são do domínio (`modules/pedidos/domain/colaboracao.py`) e não têm CHECK aqui: o limite
-- vem da config (`pedidos.colaboracao`) e muda sem migração. A F1 só grava e valida; NADA no laço lê estas colunas
-- (dependência que segura a ocorrência é a F2; papel que limita a autonomia é a F3).
--
--     pedido_dependencias(de, para, tipo, criado_em)   PRIMARY KEY (de, para)
--         DIREÇÃO: `para` DEPENDE de `de`. A seta vai de quem precisa ser feito antes (`de`) para quem espera (`para`).
--         "A ocorrência do filho fica `devida` só com a dependência comprovada" (§9): o filho é o `para`, o irmão ou o
--         pai de que ele espera é o `de`.
--         tipo = `precisa_de_resultado` (o `para` usa o que o `de` produziu) ou `depois_de` (só a ordem importa).
--         O par é a chave: repetir a mesma aresta é um só registro (o tipo da primeira vale). Sem FK: o pedido pode ser
--         apagado e a linha não pode impedir (o mesmo cuidado da 067 com `pai_id`); o domínio confere que os dois lados
--         existem e são da MESMA família na criação.
--         O índice é por `para`: a pergunta do laço (F2) é "de quem este pedido espera?".
--
--     pedidos.papel   `pesquisador` | `checador` | `redator` | `porta_voz`; NULL = pedido comum (o único valor dos
--         pedidos que existem hoje). Vocabulário sem CHECK, como a 082/085: o domínio é quem fecha a lista.
--
--     ix_pedidos_pai  os filhos de um pai (a pergunta de toda cascata de encerramento e do detalhe). `pai_id` já existe
--         desde a 067, mas ninguém o lia.
--
-- Compatível com SQLite e PostgreSQL: só TEXT e `ADD COLUMN` sem DEFAULT. Tempo em texto ISO-8601 (`criado_em`), como as
-- vizinhas (067, 072, 085, 088). Sem BEGIN/COMMIT: o executor já abre a transação.

CREATE TABLE IF NOT EXISTS pedido_dependencias (
    de        TEXT NOT NULL,       -- o pedido de que o `para` depende (vem antes)
    para      TEXT NOT NULL,       -- o pedido que espera
    tipo      TEXT NOT NULL,       -- 'precisa_de_resultado' | 'depois_de'
    criado_em TEXT NOT NULL,
    PRIMARY KEY (de, para)
);
CREATE INDEX IF NOT EXISTS ix_pedido_dependencias_para ON pedido_dependencias(para);

ALTER TABLE pedidos ADD COLUMN papel TEXT;      -- 'pesquisador' | 'checador' | 'redator' | 'porta_voz'; NULL = comum

CREATE INDEX IF NOT EXISTS ix_pedidos_pai ON pedidos(pai_id);
