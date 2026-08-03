import { useCallback, useEffect, useRef, useState } from 'react';
import {
  App,
  Button,
  Card,
  Empty,
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
import { DeleteOutlined, DownloadOutlined, PlusOutlined, PoweroffOutlined } from '@ant-design/icons';
import { petsApi } from '@/lib/api';
import type { PetPackage, PetVisibility, UserPet } from '@/lib/types';
import PetCanvas from '@/components/pet/PetCanvas';
import { PET_STATE_LABELS, rowName, usePetStore } from '@/stores/petStore';

const PET_STATES = ['idle', 'think', 'work', 'wait', 'celebrate', 'sad', 'sleep', 'happy'] as const;

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

function PackageCard({
  pkg,
  mine,
  adopted,
  onAdopt,
  onChanged,
  onDeleted,
  onEditMapping,
}: {
  pkg: PetPackage;
  mine?: boolean;
  adopted?: boolean;
  onAdopt?: (pkg: PetPackage) => void;
  onChanged?: (pkg: PetPackage) => void;
  onDeleted?: (pkg: PetPackage) => void;
  onEditMapping?: (pkg: PetPackage) => void;
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
  const fileRef = useRef<HTMLInputElement>(null);

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

  // 点击宠物互动后，store 里的 activePet 经验/等级会更新——同步进「我的宠物」列表
  useEffect(() => {
    if (!activePet) return;
    setMyPets((prev) => prev.map((p) => (p.id === activePet.id ? activePet : p)));
  }, [activePet]);

  const handleUpload = async (file: File) => {
    try {
      const pkg = await petsApi.upload(file, { idle: 0 });
      message.success(`「${pkg.display_name}」上传成功，请为它分配动画状态`);
      setMyPackages((prev) => [pkg, ...prev]);
      setMappingPkg(pkg); // 上传后引导完成行映射
      void reload();
    } catch (e) {
      message.error(e instanceof Error ? e.message : '上传失败');
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
          <Button type="primary" icon={<PlusOutlined />} onClick={() => fileRef.current?.click()}>
            上传宠物包
          </Button>
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
                      onChanged={(updated) =>
                        setMyPackages((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
                      }
                      onDeleted={(deleted) =>
                        setMyPackages((prev) => prev.filter((p) => p.id !== deleted.id))
                      }
                      onEditMapping={setMappingPkg}
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
    </div>
  );
}
