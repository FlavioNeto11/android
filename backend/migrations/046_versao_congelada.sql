-- Segunda camada da imutabilidade (ADR-034): o BANCO recusa mudar ou apagar versão de habilidade que já saiu de
-- rascunho. A camada obrigatória é o repositório (`SqlSkillRepository`) + `content_hash` conferido na leitura.
--
-- Separada de propósito: é a primeira migração com corpo entre cifrões no PostgreSQL (o divisor suporta, nenhuma
-- migração usou), e a corrida em PostgreSQL do CI só roda agendada ou manual. Se falhar num deploy, o backend não
-- sobe; por isso só entra na produção depois de verde num `workflow_dispatch`.
--
-- No PostgreSQL o erro sai com SQLSTATE 23000 (integrity_constraint_violation): psycopg o entrega como
-- IntegrityError, a mesma família do RAISE(ABORT) do SQLite, e INTEGRITY_ERRORS pega os dois.
-- Os dois só disparam em MUDANÇA real (`IS NOT` no SQLite = `IS DISTINCT FROM`): `SET content = content` numa
-- transição de estado passa nos dois, em vez de passar num banco e abortar no outro.

-- @dialect:sqlite
CREATE TRIGGER IF NOT EXISTS skill_versions_congelada
BEFORE UPDATE OF content, content_hash, schema_version, command_template, match_key, skill_id, version
ON skill_versions
WHEN OLD.state <> 'draft'
 AND (NEW.content IS NOT OLD.content OR NEW.content_hash IS NOT OLD.content_hash
      OR NEW.schema_version IS NOT OLD.schema_version OR NEW.command_template IS NOT OLD.command_template
      OR NEW.match_key IS NOT OLD.match_key OR NEW.skill_id IS NOT OLD.skill_id OR NEW.version IS NOT OLD.version)
BEGIN
    SELECT RAISE(ABORT, 'versao de habilidade fora de rascunho nao muda de conteudo');
END;
CREATE TRIGGER IF NOT EXISTS skill_versions_sem_apagar
BEFORE DELETE ON skill_versions
WHEN OLD.state <> 'draft'
BEGIN
    SELECT RAISE(ABORT, 'versao de habilidade fora de rascunho nao se apaga');
END;
-- @dialect:end

-- @dialect:postgres
CREATE OR REPLACE FUNCTION skill_versions_congelada() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state <> 'draft' THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'versao de habilidade % fora de rascunho nao se apaga', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.content IS DISTINCT FROM OLD.content OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
           OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
           OR NEW.command_template IS DISTINCT FROM OLD.command_template
           OR NEW.match_key IS DISTINCT FROM OLD.match_key
           OR NEW.skill_id IS DISTINCT FROM OLD.skill_id OR NEW.version IS DISTINCT FROM OLD.version THEN
            RAISE EXCEPTION 'versao de habilidade % fora de rascunho nao muda de conteudo', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER skill_versions_congelada BEFORE UPDATE OR DELETE ON skill_versions
    FOR EACH ROW EXECUTE FUNCTION skill_versions_congelada();
-- @dialect:end
