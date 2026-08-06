import { useCallback, useEffect, useRef, useState } from 'react';
import {
  App,
  Button,
  Card,
  Dropdown,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Switch,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  DeleteOutlined,
  DownloadOutlined,
  FolderOpenOutlined,
  PlusOutlined,
  PoweroffOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import JSZip from 'jszip';
import { agentsApi, petsApi } from '@/lib/api';
import type { Agent, PetPackage, PetVisibility, UserPet } from '@/lib/types';
import PetCanvas from '@/components/pet/PetCanvas';
import { PET_STATE_LABELS, rowName, usePetStore } from '@/stores/petStore';

const PET_STATES = ['idle', 'think', 'work', 'wait', 'celebrate', 'sad', 'sleep', 'happy', 'run_right', 'run_left'] as const;

const STATE_LABELS = PET_STATE_LABELS;

const VISIBILITY_OPTIONS = [
  { value: 'private', label: '仅自己可见' },
  { value: 'tenant', label: '租户内可见' },
  { value: 'public', label: '全平台公开' },
];

/** 行映射编辑弹窗：逐行预览动画并分配状态 */
function RowMappingModal({
  pkg,
  open,
  onClose,
  onSaved,
}: {
  pkg: PetPackage;
  open: boolean;
  onClose: () => void;
  onSaved: (pkg: PetPackage) => void;
}) {
  const { message } = App.useApp();
  // row → state（反向建模，一个行最多分配一个状态；多行分配同状态时最后一行生效）
  const [assignments, setAssignments] = useState<Record<number, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      const init: Record<number, string> = {};
      for (const state of PET_STATES) {
        const row = pkg.row_mapping[state];
        if (row !== undefined) init[row] = state;
      }
      setAssignments(init);
    }
  }, [open, pkg]);

  const handleSave = async () => {
    const mapping: Record<string, number> = {};
    for (const [row, state] of Object.entries(assignments)) {
      if (state) mapping[state] = Number(row);
    }
    if (mapping.idle === undefined) {
      message.error('必须至少为一行分配「待机」状态');
      return;
    }
    setSaving(true);
    try {
      const updated = await petsApi.setRowMapping(pkg.id, mapping);
      message.success('动画映射已保存');
      onSaved(updated);
      onClose();
    } catch (e) {
      message.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={`动画映射 — ${pkg.display_name}`}
      open={open}
      onCancel={onClose}
      onOk={handleSave}
      confirmLoading={saving}
      okText="保存"
      width={720}
    >
      <Typography.Paragraph type="secondary" className="text-xs">
        Codex 宠物包不包含动画语义，请为精灵图的每一行分配对应的平台状态。未分配的状态将降级使用「待机」动画。
      </Typography.Paragraph>
      <div className="grid grid-cols-4 gap-3">
        {Array.from({ length: pkg.row_count }, (_, row) => (
          <div key={row} className="flex flex-col items-center gap-1 rounded-lg border border-border p-2">
            <PetCanvas pkg={pkg} mood="idle" fixedRow={row} size={72} />
            <span className="text-xs text-muted-foreground">行 {row} · {rowName(pkg, row)}</span>
            <Select
              size="small"
              className="w-full"
              placeholder="未分配"
              allowClear
              value={assignments[row]}
              onChange={(v) => setAssignments((prev) => ({ ...prev, [row]: v }))}
              options={PET_STATES.map((s) => ({ value: s, label: STATE_LABELS[s] }))}
            />
          </div>
        ))}
      </div>
    </Modal>
  );
}

/** 行 → 平台状态 反查（包级 row_mapping） */
function stateForRow(pkg: PetPackage, row: number): string | undefined {
  for (const [state, r] of Object.entries(pkg.row_mapping)) {
    if (state !== '_row_frames' && r === row) return state;
  }
  return undefined;
}

/** 动作名称编辑弹窗：包级改目录名；实例级改覆盖名 + 状态映射 */
function ActionNameModal({
  title,
  pkg,
  pet,
  open,
  onClose,
  onSaved,
}: {
  title: string;
  pkg: PetPackage;
  pet?: UserPet;
  open: boolean;
  onClose: () => void;
  onSaved: (updated: PetPackage | UserPet) => void;
}) {
  const { message } = App.useApp();
  const [names, setNames] = useState<Record<number, string>>({});
  const [states, setStates] = useState<Record<number, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    const initNames: Record<number, string> = {};
    const initStates: Record<number, string> = {};
    const resolved = pet?.actions ?? [];
    for (let row = 0; row < pkg.row_count; row++) {
      const act = resolved.find((a) => a.row === row);
      initNames[row] = act?.name ?? rowName(pkg, row);
      initStates[row] = act?.state ?? stateForRow(pkg, row) ?? '';
    }
    setNames(initNames);
    setStates(initStates);
  }, [open, pkg, pet]);

  const handleSave = async () => {
    const seen = new Set<string>();
    for (let row = 0; row < pkg.row_count; row++) {
      const n = (names[row] ?? '').trim();
      if (!n) {
        message.error(`第 ${row} 行动作名不能为空`);
        return;
      }
      if (seen.has(n)) {
        message.error(`动作名重复：${n}`);
        return;
      }
      seen.add(n);
    }
    setSaving(true);
    try {
      if (pet) {
        // 实例级：只把与包级不同的行作为覆盖名；状态映射只提交与包级不同的
        const aliases: Record<string, string> = {};
        const pkgActions = pkg.actions ?? {};
        for (let row = 0; row < pkg.row_count; row++) {
          const n = (names[row] ?? '').trim();
          const pkgName = pkgActions[String(row)]?.name ?? rowName(pkg, row);
          if (n !== pkgName) aliases[String(row)] = n;
        }
        const baseMapping: Record<string, number> = {};
        for (const [state, r] of Object.entries(pkg.row_mapping)) {
          if (state !== '_row_frames') baseMapping[state] = r as number;
        }
        const stateMapping: Record<string, number> = {};
        for (let row = 0; row < pkg.row_count; row++) {
          const st = states[row];
          if (st && baseMapping[st] !== row) stateMapping[st] = row;
        }
        const updated = await petsApi.setPetActions(pet.id, aliases, stateMapping);
        onSaved(updated);
        message.success('动作已保存（仅对本宠物生效）');
      } else {
        const actions: Record<string, string> = {};
        for (let row = 0; row < pkg.row_count; row++) {
          actions[String(row)] = (names[row] ?? '').trim();
        }
        const updated = await petsApi.setPackageActions(pkg.id, actions);
        onSaved(updated);
        message.success('动作已保存');
      }
      onClose();
    } catch (e) {
      message.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={title}
      open={open}
      onCancel={onClose}
      onOk={handleSave}
      confirmLoading={saving}
      okText="保存"
      width={720}
    >
      <Typography.Paragraph type="secondary" className="text-xs">
        动作名称会显示在右键菜单，也是智能体触发该动作的词。给动作起个有意义的名字，智能体就能用它表达情绪。
      </Typography.Paragraph>
      <div className="grid grid-cols-2 gap-3">
        {Array.from({ length: pkg.row_count }, (_, row) => (
          <div key={row} className="flex items-center gap-2 rounded-lg border border-border p-2">
            <PetCanvas pkg={pkg} mood="idle" fixedRow={row} size={56} />
            <div className="flex flex-1 flex-col gap-1">
              <Input
                size="small"
                addonBefore={`行${row}`}
                value={names[row]}
                maxLength={20}
                onChange={(e) => setNames((prev) => ({ ...prev, [row]: e.target.value }))}
              />
              <Select
                size="small"
                placeholder="状态（可选）"
                allowClear
                value={states[row] || undefined}
                onChange={(v) => setStates((prev) => ({ ...prev, [row]: v ?? '' }))}
                options={PET_STATES.map((s) => ({ value: s, label: STATE_LABELS[s] }))}
              />
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}

function PackageCard({
  pkg,
  mine,
  adopted,
  agents,
  onAdopt,
  onChanged,
  onDeleted,
  onEditMapping,
  onEditActions,
  onDefaultAgent,
}: {
  pkg: PetPackage;
  mine?: boolean;
  adopted?: boolean;
  agents: Agent[];
  onAdopt?: (pkg: PetPackage) => void;
  onChanged?: (pkg: PetPackage) => void;
  onDeleted?: (pkg: PetPackage) => void;
  onEditMapping?: (pkg: PetPackage) => void;
  onEditActions?: (pkg: PetPackage) => void;
  onDefaultAgent?: (pkg: PetPackage, agentId: string | null) => void;
}) {
  const { message } = App.useApp();

  const handleVisibility = async (v: PetVisibility) => {
    try {
      const updated = await petsApi.setVisibility(pkg.id, v);
      message.success('可见性已更新');
      onChanged?.(updated);
    } catch (e) {
      message.error(e instanceof Error ? e.message : '更新失败');
    }
  };

  return (
    <Card
      size="small"
      className="w-64"
      cover={
        <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 16 }}>
          <PetCanvas pkg={pkg} mood="idle" size={112} />
        </div>
      }
      actions={
        mine
          ? [
              <Button key="mapping" type="link" size="small" onClick={() => onEditMapping?.(pkg)}>
                动画映射
              </Button>,
              <Button key="actions" type="link" size="small" onClick={() => onEditActions?.(pkg)}>
                动作名称
              </Button>,
              <Button
                key="download"
                type="link"
                size="small"
                icon={<DownloadOutlined />}
                title="下载原始包"
                onClick={() => {
                  void petsApi
                    .download(pkg.id, `${pkg.name}.zip`)
                    .catch((e) => message.error(e instanceof Error ? e.message : '下载失败'));
                }}
              />,
              <Popconfirm
                key="delete"
                title="删除该宠物包？"
                description="已领养的用户不受影响"
                onConfirm={async () => {
                  try {
                    await petsApi.deletePackage(pkg.id);
                    onDeleted?.(pkg);
                  } catch (e) {
                    message.error(e instanceof Error ? e.message : '删除失败');
                  }
                }}
              >
                <Button type="link" size="small" danger>
                  删除
                </Button>
              </Popconfirm>,
            ]
          : [
              <Button
                key="adopt"
                type="link"
                size="small"
                disabled={adopted}
                onClick={() => onAdopt?.(pkg)}
              >
                {adopted ? '已领养' : '领养'}
              </Button>,
            ]
      }
    >
      <Card.Meta
        title={
          <span className="flex items-center gap-1">
            {pkg.display_name}
            {pkg.visibility === 'official' && <Tag color="gold">官方</Tag>}
          </span>
        }
        description={
          <div className="flex flex-col gap-2">
            <span className="line-clamp-2 min-h-8 text-xs">{pkg.description || '—'}</span>
            {mine && (
              <Select
                size="small"
                placeholder="默认人设智能体"
                value={pkg.default_agent_id ?? undefined}
                allowClear
                options={agents.map((a) => ({ value: a.id, label: a.name }))}
                onChange={(v) => onDefaultAgent?.(pkg, v ?? null)}
              />
            )}
            {mine && pkg.visibility !== 'official' && (
              <Select
                size="small"
                value={pkg.visibility}
                options={VISIBILITY_OPTIONS}
                onChange={handleVisibility}
              />
            )}
            {mine && pkg.visibility === 'official' && <Tag color="gold">官方包</Tag>}
          </div>
        }
      />
    </Card>
  );
}

export default function PetsPage() {
  const { message } = App.useApp();
  const { enabled, setEnabled, loadActive } = usePetStore();
  const activePet = usePetStore((s) => s.activePet);
  const [myPets, setMyPets] = useState<UserPet[]>([]);
  const [myPackages, setMyPackages] = useState<PetPackage[]>([]);
  const [market, setMarket] = useState<PetPackage[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [mappingPkg, setMappingPkg] = useState<PetPackage | null>(null);
  const [actionNameTarget, setActionNameTarget] = useState<{
    pkg: PetPackage;
    pet?: UserPet;
  } | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    agentsApi.list().then(setAgents).catch(() => {});
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [pets, packages, marketList] = await Promise.all([
        petsApi.myPets(),
        petsApi.myPackages(),
        petsApi.market(),
      ]);
      setMyPets(pets);
      setMyPackages(packages);
      setMarket(marketList);
    } catch (e) {
      message.error(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // 点击互动/绑定后 activePet 更新——同步进「我的宠物」列表
  useEffect(() => {
    if (!activePet) return;
    setMyPets((prev) => prev.map((p) => (p.id === activePet.id ? activePet : p)));
  }, [activePet]);

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const pkg = await petsApi.upload(file, { idle: 0 });
      message.success(`「${pkg.display_name}」上传成功，请为它分配动画状态`);
      setMyPackages((prev) => [pkg, ...prev]);
      setMappingPkg(pkg); // 上传后引导完成行映射
      void reload();
    } catch (e) {
      message.error(e instanceof Error ? e.message : '上传失败');
    } finally {
      setUploading(false);
    }
  };

  /** 选择整个文件夹，打包成 zip 后走同一上传接口 */
  const handleFolderChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (files.length === 0) return;
    setUploading(true);
    try {
      const rel = (files[0] as File & { webkitRelativePath?: string }).webkitRelativePath;
      const folderName = rel?.split('/')[0] || 'pet';
      const zip = new JSZip();
      const added = new Set<string>();
      for (const f of files) {
        const p = (f as File & { webkitRelativePath?: string }).webkitRelativePath;
        if (!p || added.has(p)) continue;
        added.add(p);
        zip.file(p, f);
      }
      if (added.size === 0) {
        message.warning('所选文件夹没有可上传的文件');
        return;
      }
      const blob = await zip.generateAsync({ type: 'blob' });
      await handleUpload(new File([blob], `${folderName}.zip`, { type: 'application/zip' }));
    } catch (err) {
      message.error(err instanceof Error ? err.message : '打包失败');
    } finally {
      setUploading(false);
    }
  };

  const handleAdopt = async (pkg: PetPackage) => {
    try {
      await petsApi.adopt(pkg.id);
      message.success(`已领养「${pkg.display_name}」`);
      void reload();
    } catch (e) {
      message.error(e instanceof Error ? e.message : '领养失败');
    }
  };

  const handleActivate = async (pet: UserPet) => {
    setBusyAction(`${pet.id}:activate`);
    try {
      await petsApi.activate(pet.id);
      message.success(`已激活「${pet.package.display_name}」`);
      await loadActive();
      void reload();
    } catch (e) {
      message.error(e instanceof Error ? e.message : '激活失败');
    } finally {
      setBusyAction(null);
    }
  };

  const handleRemove = async (pet: UserPet) => {
    setBusyAction(`${pet.id}:remove`);
    try {
      await petsApi.remove(pet.id);
      message.success(`已移除「${pet.package.display_name}」`);
      if (pet.is_active) await loadActive();
      void reload();
    } catch (e) {
      message.error(e instanceof Error ? e.message : '移除失败');
    } finally {
      setBusyAction(null);
    }
  };

  const handleDeactivate = async () => {
    if (!activePet) return;
    setBusyAction(`${activePet.id}:activate`);
    try {
      await petsApi.deactivate();
      message.success('已取消激活');
      await loadActive();
      void reload();
    } catch (e) {
      message.error(e instanceof Error ? e.message : '操作失败');
    } finally {
      setBusyAction(null);
    }
  };

  const handleBindAgent = async (pet: UserPet, agentId: string | null) => {
    try {
      const updated = await petsApi.bindAgent(pet.id, agentId);
      message.success(agentId ? '已绑定智能体' : '已解绑智能体');
      setMyPets((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      if (pet.is_active) await loadActive();
    } catch (e) {
      message.error(e instanceof Error ? e.message : '绑定失败');
    }
  };

  const handleDefaultAgent = async (pkg: PetPackage, agentId: string | null) => {
    try {
      const updated = await petsApi.setPackageDefaultAgent(pkg.id, agentId);
      message.success(agentId ? '已设置默认人设' : '已清除默认人设');
      setMyPackages((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    } catch (e) {
      message.error(e instanceof Error ? e.message : '设置失败');
    }
  };

  const handleActionsSaved = (updated: PetPackage | UserPet) => {
    if ('package_id' in updated) {
      setMyPets((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      if (updated.is_active) void loadActive();
    } else {
      setMyPackages((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spin />
      </div>
    );
  }

  const adoptedIds = new Set(myPets.map((p) => p.package_id));

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mb-4 flex items-center justify-between">
        <Typography.Title level={4} className="!mb-0">
          宠物
        </Typography.Title>
        <div className="flex flex-wrap items-center gap-4">
          <span className="text-sm text-muted-foreground">
            显示悬浮宠物 <Switch checked={enabled} onChange={setEnabled} size="small" />
          </span>
          <Dropdown
            menu={{
              items: [
                { key: 'zip', icon: <UploadOutlined />, label: '上传 zip 文件' },
                { key: 'folder', icon: <FolderOpenOutlined />, label: '选择文件夹上传' },
              ],
              onClick: ({ key }) => {
                if (key === 'zip') fileRef.current?.click();
                else folderRef.current?.click();
              },
            }}
          >
            <Button type="primary" icon={<PlusOutlined />} loading={uploading}>
              上传宠物包
            </Button>
          </Dropdown>
          <input
            ref={fileRef}
            type="file"
            accept=".zip"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void handleUpload(f);
              e.target.value = '';
            }}
          />
          <input
            ref={folderRef}
            type="file"
            {...({ webkitdirectory: '' } as React.InputHTMLAttributes<HTMLInputElement>)}
            hidden
            onChange={handleFolderChange}
          />
        </div>
      </div>

      <Tabs
        items={[
          {
            key: 'mine',
            label: `我的宠物 (${myPets.length})`,
            children:
              myPets.length === 0 ? (
                <Empty description="还没有宠物，去市场领养一只吧" />
              ) : (
                <div className="flex flex-wrap gap-4">
                  {myPets.map((pet) => (
                    <Card
                      key={pet.id}
                      size="small"
                      className="w-64"
                      cover={
                        <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 16 }}>
                          <PetCanvas pkg={pet.package} mood="idle" size={112} />
                        </div>
                      }
                      actions={[
                        pet.is_active ? (
                          <Tooltip key="off" title="取消激活">
                            <Button
                              type="text"
                              size="small"
                              loading={busyAction === `${pet.id}:activate`}
                              icon={<PoweroffOutlined style={{ color: '#52c41a' }} />}
                              onClick={handleDeactivate}
                            />
                          </Tooltip>
                        ) : (
                          <Tooltip key="on" title="激活">
                            <Button
                              type="text"
                              size="small"
                              loading={busyAction === `${pet.id}:activate`}
                              icon={<PoweroffOutlined />}
                              onClick={() => void handleActivate(pet)}
                            />
                          </Tooltip>
                        ),
                        <Popconfirm
                          key="remove"
                          title="移除该宠物？"
                          description="移除后可重新领养"
                          onConfirm={() => void handleRemove(pet)}
                        >
                          <Tooltip title="删除">
                            <Button
                              type="text"
                              size="small"
                              danger
                              loading={busyAction === `${pet.id}:remove`}
                              icon={<DeleteOutlined />}
                            />
                          </Tooltip>
                        </Popconfirm>,
                      ]}
                    >
                      <Card.Meta
                        title={
                          <span className="flex items-center gap-1">
                            {pet.package.display_name}
                            {pet.is_active && <Tag color="green">已激活</Tag>}
                          </span>
                        }
                        description={
                          <div className="flex flex-col gap-2">
                            <div className="flex items-center gap-2">
                              <Select
                                size="small"
                                className="flex-1"
                                placeholder="绑定智能体"
                                value={pet.agent?.id ?? undefined}
                                allowClear
                                options={agents.map((a) => ({ value: a.id, label: a.name }))}
                                onChange={(v) => void handleBindAgent(pet, v ?? null)}
                              />
                              <Tooltip
                                title={
                                  pet.agent
                                    ? pet.agent.level === 'instance'
                                      ? '实例绑定'
                                      : '包级默认'
                                    : '未绑定'
                                }
                              >
                                <Tag
                                  className="shrink-0"
                                  color={
                                    pet.agent
                                      ? pet.agent.level === 'instance'
                                        ? 'blue'
                                        : 'purple'
                                      : 'default'
                                  }
                                >
                                  {pet.agent ? (pet.agent.level === 'instance' ? '实例' : '包级') : '未绑定'}
                                </Tag>
                              </Tooltip>
                            </div>
                            <Button
                              type="link"
                              size="small"
                              className="!h-auto !p-0 text-left"
                              onClick={() => setActionNameTarget({ pkg: pet.package, pet })}
                            >
                              动作名称
                            </Button>
                          </div>
                        }
                      />
                    </Card>
                  ))}
                </div>
              ),
          },
          {
            key: 'packages',
            label: `我的上传 (${myPackages.length})`,
            children:
              myPackages.length === 0 ? (
                <Empty description="还没有上传过宠物包，支持 Codex 格式（pet.json + spritesheet）" />
              ) : (
                <div className="flex flex-wrap gap-4">
                  {myPackages.map((pkg) => (
                    <PackageCard
                      key={pkg.id}
                      pkg={pkg}
                      mine
                      agents={agents}
                      onChanged={(updated) =>
                        setMyPackages((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
                      }
                      onDeleted={(deleted) =>
                        setMyPackages((prev) => prev.filter((p) => p.id !== deleted.id))
                      }
                      onEditMapping={setMappingPkg}
                      onEditActions={(p) => setActionNameTarget({ pkg: p })}
                      onDefaultAgent={handleDefaultAgent}
                    />
                  ))}
                </div>
              ),
          },
          {
            key: 'market',
            label: '宠物市场',
            children:
              market.length === 0 ? (
                <Empty description="暂无可见的宠物包" />
              ) : (
                <div className="flex flex-wrap gap-4">
                  {market.map((pkg) => (
                    <PackageCard
                      key={pkg.id}
                      pkg={pkg}
                      agents={agents}
                      adopted={adoptedIds.has(pkg.id)}
                      onAdopt={handleAdopt}
                    />
                  ))}
                </div>
              ),
          },
        ]}
      />

      {mappingPkg && (
        <RowMappingModal
          pkg={mappingPkg}
          open
          onClose={() => setMappingPkg(null)}
          onSaved={(updated) =>
            setMyPackages((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
          }
        />
      )}

      {actionNameTarget && (
        <ActionNameModal
          title={
            actionNameTarget.pet
              ? `动作名称 — ${actionNameTarget.pet.package.display_name}`
              : `动作名称 — ${actionNameTarget.pkg.display_name}`
          }
          pkg={actionNameTarget.pkg}
          pet={actionNameTarget.pet}
          open
          onClose={() => setActionNameTarget(null)}
          onSaved={handleActionsSaved}
        />
      )}
    </div>
  );
}
