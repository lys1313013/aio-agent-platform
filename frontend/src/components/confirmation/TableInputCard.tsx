import { useMemo, useState } from 'react';
import { Button, Input, InputNumber, Select, Switch } from 'antd';
import { DeleteOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons';
import type { ConfirmationResponse, TableColumnDef, TableSchema } from '@/lib/types';

interface Props {
  schema: TableSchema;
  onSubmit: (response: ConfirmationResponse) => void;
}

type Row = Record<string, unknown> & { __key: string };

let rowSeq = 0;
const nextKey = () => `row-${Date.now()}-${rowSeq++}`;

function buildDefaultRow(columns: TableColumnDef[]): Row {
  const row: Row = { __key: nextKey() };
  for (const col of columns) {
    row[col.key] = col.default ?? (col.type === 'boolean' ? false : '');
  }
  return row;
}

function isEmpty(value: unknown): boolean {
  return value === undefined || value === null || value === '';
}

export default function TableInputCard({ schema, onSubmit }: Props) {
  const columns = schema.columns || [];
  const minRows = schema.min_rows ?? 1;
  const maxRows = schema.max_rows;
  const allowAdd = schema.allow_add_row ?? true;
  const allowDelete = schema.allow_delete_row ?? true;

  const [rows, setRows] = useState<Row[]>(() => {
    const initial = (schema.rows || []).map((r) => ({ ...r, __key: nextKey() }) as Row);
    while (initial.length < minRows) {
      initial.push(buildDefaultRow(columns));
    }
    return initial;
  });

  const [note, setNote] = useState('');

  const updateCell = (rowKey: string, colKey: string, value: unknown) => {
    setRows((prev) =>
      prev.map((r) => (r.__key === rowKey ? { ...r, [colKey]: value } : r)),
    );
  };

  const addRow = () => {
    if (maxRows !== undefined && rows.length >= maxRows) return;
    setRows((prev) => [...prev, buildDefaultRow(columns)]);
  };

  const deleteRow = (rowKey: string) => {
    if (rows.length <= minRows) return;
    setRows((prev) => prev.filter((r) => r.__key !== rowKey));
  };

  const renderCell = (col: TableColumnDef, row: Row) => {
    const value = row[col.key];
    const onChange = (v: unknown) => updateCell(row.__key, col.key, v);

    switch (col.type) {
      case 'number':
        return (
          <InputNumber
            size="small"
            className="w-full"
            value={value as number | null}
            placeholder={col.placeholder}
            onChange={(v) => onChange(v)}
          />
        );
      case 'select':
        return (
          <Select
            size="small"
            className="w-full"
            value={(value as string) || undefined}
            placeholder={col.placeholder}
            options={(col.options || []).map((o) => ({ label: o, value: o }))}
            onChange={(v) => onChange(v)}
            allowClear
          />
        );
      case 'boolean':
        return (
          <Switch
            size="small"
            checked={Boolean(value)}
            onChange={(v) => onChange(v)}
          />
        );
      case 'date':
        return (
          <input
            type="date"
            className="ant-input ant-input-sm w-full rounded border border-gray-300 px-2 py-0.5 text-sm dark:border-gray-600 dark:bg-transparent"
            value={(value as string) || ''}
            onChange={(e) => onChange(e.target.value)}
          />
        );
      default:
        return (
          <Input
            size="small"
            value={(value as string) || ''}
            placeholder={col.placeholder}
            onChange={(e) => onChange(e.target.value)}
          />
        );
    }
  };

  const validationError = useMemo(() => {
    if (rows.length < minRows) return `至少需要 ${minRows} 条记录`;
    for (const row of rows) {
      for (const col of columns) {
        if (col.required && isEmpty(row[col.key])) {
          return `「${col.title}」为必填项`;
        }
      }
    }
    return null;
  }, [rows, columns, minRows]);

  const handleSubmit = () => {
    if (validationError) return;
    const table_data = rows.map(({ __key, ...rest }) => rest);
    onSubmit({
      status: 'approved',
      table_data,
      user_input: note.trim() || undefined,
    });
  };

  const canDelete = allowDelete && rows.length > minRows;

  return (
    <div className="space-y-3">
      <div className="space-y-3">
        {rows.map((row, idx) => (
          <div
            key={row.__key}
            className="rounded-lg border border-gray-200 bg-muted/10 p-3 dark:border-gray-700"
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground">
                记录 {idx + 1}
              </span>
              {allowDelete && (
                <Button
                  type="text"
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                  disabled={!canDelete}
                  onClick={() => deleteRow(row.__key)}
                />
              )}
            </div>
            <div className="space-y-2">
              {columns.map((col) => (
                <div key={col.key} className="flex items-start gap-3">
                  <label className="w-24 flex-shrink-0 pt-1 text-sm text-muted-foreground">
                    {col.title}
                    {col.required && <span className="text-red-500"> *</span>}
                  </label>
                  <div className="min-w-0 flex-1">{renderCell(col, row)}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div>
        <label className="mb-1 block text-sm text-muted-foreground">补充说明（选填）</label>
        <Input.TextArea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="可填写任意备注或补充说明..."
          autoSize={{ minRows: 2, maxRows: 6 }}
        />
      </div>

      <div className="flex items-center justify-between gap-2">
        <div>
          {allowAdd && (
            <Button
              size="small"
              icon={<PlusOutlined />}
              disabled={maxRows !== undefined && rows.length >= maxRows}
              onClick={addRow}
            >
              新增记录
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {validationError && (
            <span className="text-xs text-red-500">{validationError}</span>
          )}
          <Button size="small" onClick={() => onSubmit({ status: 'rejected' })}>
            跳过
          </Button>
          <Button
            type="primary"
            size="small"
            icon={<SendOutlined />}
            disabled={!!validationError}
            onClick={handleSubmit}
          >
            提交
          </Button>
        </div>
      </div>
    </div>
  );
}
