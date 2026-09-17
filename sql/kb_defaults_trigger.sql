-- 新建知识库时自动补齐模型与分块配置。
--
-- 背景：通过 MCP 的 create_knowledge_base 或 API 建库时若不传 embedding_model_id，
-- 库会以空模型建成，此后上传的文档卡在 parse_status='processing' 永不完成
-- （客户端轮询看起来就是「服务端卡住」），超过 2h10m 才被 housekeeping 标成 failed。
-- 这个字段事后无法通过 PUT 接口修改，只能改库，所以在入库前就补好。
--
-- 只在字段为空时介入，界面正常建库（会传模型 ID）不受影响。

CREATE OR REPLACE FUNCTION kb_fill_model_defaults() RETURNS trigger AS $$
DECLARE
  emb_id  text;
  sum_id  text;
BEGIN
  IF coalesce(NEW.embedding_model_id, '') = '' THEN
    SELECT id INTO emb_id FROM models
    WHERE type = 'Embedding' AND status = 'active' AND deleted_at IS NULL
    ORDER BY is_default DESC, created_at
    LIMIT 1;
    IF emb_id IS NOT NULL THEN
      NEW.embedding_model_id := emb_id;
      RAISE NOTICE 'kb_defaults: 知识库 % 自动填入 embedding 模型 %', NEW.name, emb_id;
    END IF;
  END IF;

  IF coalesce(NEW.summary_model_id, '') = '' THEN
    SELECT id INTO sum_id FROM models
    WHERE type = 'KnowledgeQA' AND status = 'active' AND deleted_at IS NULL
    ORDER BY is_default DESC, created_at
    LIMIT 1;
    IF sum_id IS NOT NULL THEN
      NEW.summary_model_id := sum_id;
    END IF;
  END IF;

  -- chunk_size 为 0 或缺失时分块器切不出内容
  IF coalesce((NEW.chunking_config ->> 'chunk_size')::int, 0) = 0 THEN
    NEW.chunking_config := jsonb_build_object(
      'strategy',          'auto',
      'chunk_size',        2048,
      'chunk_overlap',     80,
      'child_chunk_size',  384,
      'parent_chunk_size', 4096,
      'separators',        jsonb_build_array(E'\n\n', E'\n', '。', '！', '？', ';', '；')
    );
  END IF;

  IF NEW.extract_config IS NULL THEN
    NEW.extract_config := '{"enabled": false}'::jsonb;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_kb_fill_model_defaults ON knowledge_bases;
CREATE TRIGGER trg_kb_fill_model_defaults
  BEFORE INSERT ON knowledge_bases
  FOR EACH ROW
  EXECUTE FUNCTION kb_fill_model_defaults();
