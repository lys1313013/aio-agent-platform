import { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate } from 'react-router-dom';
import {
  ApartmentOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  RobotOutlined,
  TeamOutlined,
  DatabaseOutlined,
  UserAddOutlined,
} from '@ant-design/icons';
import {
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { tenantsApi, usersApi } from '@/lib/api';
import type { AdminUser, Tenant, TenantUser } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';

const { Text } = Typography;

export default function TenantManagementPage() {
  const { message } = App.useApp();
  const role = useAuthStore((state) => state.role);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [tenantModalOpen, setTenantModalOpen] = useState(false);
  const [editingTenant, setEditingTenant] = useState<Tenant | null>(null);
  const [selectedTenant, setSelectedTenant] = useState<Tenant | null>(null);
  const [users, setUsers] = useState<TenantUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [memberModalOpen, setMemberModalOpen] = useState(false);
  const [allUsers, setAllUsers] = useState<AdminUser[]>([]);
  const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
  const [tenantForm] = Form.useForm();

  const loadTenants = useCallback(async () => {
    setLoading(true);
    try {
      setTenants(await tenantsApi.list());
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载租户失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  const loadUsers = useCallback(async (tenant: Tenant) => {
    setUsersLoading(true);
    try {
      setUsers(await tenantsApi.listUsers(tenant.id));
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载租户用户失败');
    } finally {
      setUsersLoading(false);
    }
  }, [message]);

  useEffect(() => {
    if (role === 'superadmin') void loadTenants();
  }, [role, loadTenants]);

  const summary = useMemo(
    () => ({
      activeTenants: tenants.filter((tenant) => tenant.is_active).length,
      users: tenants.reduce((total, tenant) => total + tenant.users_count, 0),
      agents: tenants.reduce((total, tenant) => total + tenant.agents_count, 0),
      knowledgeBases: tenants.reduce(
        (total, tenant) => total + tenant.knowledge_bases_count,
        0,
      ),
    }),
    [tenants],
  );

  if (role !== 'superadmin') {
    return <Navigate to="/" replace />;
  }

  const openTenantModal = (tenant?: Tenant) => {
    setEditingTenant(tenant ?? null);
    if (tenant) {
      tenantForm.setFieldsValue({
        name: tenant.name,
        slug: tenant.slug,
        is_active: tenant.is_active,
      });
    } else {
      tenantForm.resetFields();
      tenantForm.setFieldsValue({ is_active: true });
    }
    setTenantModalOpen(true);
  };

  const saveTenant = async () => {
    try {
      const values = await tenantForm.validateFields();
      setSaving(true);
      if (editingTenant) {
        await tenantsApi.update(editingTenant.id, values);
        message.success('租户已更新');
      } else {
        await tenantsApi.create({ name: values.name, slug: values.slug });
        message.success('租户已创建');
      }
      setTenantModalOpen(false);
      await loadTenants();
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error instanceof Error ? error.message : '保存失败');
      }
    } finally {
      setSaving(false);
    }
  };

  const openUsers = async (tenant: Tenant) => {
    setSelectedTenant(tenant);
    setUsers([]);
    await loadUsers(tenant);
  };

  const openMemberModal = async () => {
    try {
      setAllUsers(await usersApi.list());
      setSelectedUserIds([]);
      setMemberModalOpen(true);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载用户失败');
    }
  };

  const assignUsers = async () => {
    if (!selectedTenant || selectedUserIds.length === 0) return;
    try {
      setSaving(true);
      const result = await tenantsApi.assignUsers(selectedTenant.id, selectedUserIds);
      message.success(`已添加 ${result.added_count} 名用户`);
      setMemberModalOpen(false);
      await Promise.all([loadUsers(selectedTenant), loadTenants()]);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '添加成员失败');
    } finally {
      setSaving(false);
    }
  };

  const tenantColumns: ColumnsType<Tenant> = [
    {
      title: '租户',
      key: 'tenant',
      render: (_, tenant) => (
        <button
          type="button"
          className="min-h-11 text-left cursor-pointer"
          onClick={() => void openUsers(tenant)}
        >
          <div className="font-medium text-foreground">{tenant.name}</div>
          <Text type="secondary" className="text-xs">{tenant.slug}</Text>
        </button>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 100,
      render: (active: boolean) => (
        <Tag color={active ? 'success' : 'default'}>
          {active ? '已启用' : '已停用'}
        </Tag>
      ),
    },
    { title: '用户', dataIndex: 'users_count', width: 90 },
    { title: '智能体', dataIndex: 'agents_count', width: 90 },
    { title: '知识库', dataIndex: 'knowledge_bases_count', width: 90 },
    {
      title: '操作',
      key: 'actions',
      width: 128,
      render: (_, tenant) => (
        <Space size={4}>
          <Tooltip title="编辑租户">
            <Button
              aria-label={`编辑租户 ${tenant.name}`}
              type="text"
              icon={<EditOutlined />}
              onClick={() => openTenantModal(tenant)}
            />
          </Tooltip>
          <Popconfirm
            title="删除租户？"
            description="仅空租户可以删除，此操作不可撤销。"
            okText="删除"
            okType="danger"
            cancelText="取消"
            onConfirm={async () => {
              try {
                await tenantsApi.delete(tenant.id);
                message.success('租户已删除');
                await loadTenants();
              } catch (error) {
                message.error(error instanceof Error ? error.message : '删除失败');
              }
            }}
          >
            <Tooltip title="删除空租户">
              <Button
                aria-label={`删除租户 ${tenant.name}`}
                type="text"
                danger
                icon={<DeleteOutlined />}
              />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const userColumns: ColumnsType<TenantUser> = [
    {
      title: '用户',
      key: 'user',
      render: (_, user) => (
        <div>
          <div className="font-medium">
            {user.display_name || user.username}
            {user.display_name && (
              <Text type="secondary" className="ml-2 text-xs">@{user.username}</Text>
            )}
          </div>
          <Text type="secondary" className="text-xs">{user.email}</Text>
        </div>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role',
      width: 120,
      render: (userRole: TenantUser['role']) => (
        <Tag color={userRole === 'superadmin' ? 'purple' : userRole === 'admin' ? 'blue' : 'default'}>
          {userRole === 'superadmin' ? '超级管理员' : userRole === 'admin' ? '管理员' : '用户'}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 100,
      render: (active: boolean) => (
        <Tag color={active ? 'success' : 'default'}>{active ? '正常' : '已禁用'}</Tag>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      render: (_, user) => (
        <Popconfirm
          title="移出租户？"
          description="用户仍保留在其他租户中，并且必须至少属于一个租户。"
          okText="移出"
          okType="danger"
          cancelText="取消"
          onConfirm={async () => {
            if (!selectedTenant) return;
            try {
              await tenantsApi.removeUser(selectedTenant.id, user.id);
              message.success('用户已移出租户');
              await Promise.all([loadUsers(selectedTenant), loadTenants()]);
            } catch (error) {
              message.error(error instanceof Error ? error.message : '移出失败');
            }
          }}
        >
          <Button
            type="text"
            danger
            icon={<DeleteOutlined />}
            aria-label={`将用户 ${user.username} 移出租户`}
          />
        </Popconfirm>
      ),
    },
  ];

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
        <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-bold text-foreground">租户管理</h1>
            <Text type="secondary">管理平台租户，并从已有用户中选择租户成员</Text>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openTenantModal()}>
            新建租户
          </Button>
        </div>

        <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[
            { label: '启用租户', value: summary.activeTenants, icon: <ApartmentOutlined /> },
            { label: '成员关系', value: summary.users, icon: <TeamOutlined /> },
            { label: '智能体', value: summary.agents, icon: <RobotOutlined /> },
            { label: '知识库', value: summary.knowledgeBases, icon: <DatabaseOutlined /> },
          ].map((item) => (
            <Card key={item.label} size="small">
              <div className="flex items-center gap-3 p-1">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  {item.icon}
                </div>
                <div>
                  <div className="text-xl font-semibold tabular-nums">{item.value}</div>
                  <Text type="secondary" className="text-xs">{item.label}</Text>
                </div>
              </div>
            </Card>
          ))}
        </div>

        <Card>
          {loading ? (
            <div className="flex min-h-64 items-center justify-center"><Spin /></div>
          ) : tenants.length === 0 ? (
            <Empty description="暂无租户">
              <Button type="primary" onClick={() => openTenantModal()}>新建租户</Button>
            </Empty>
          ) : (
            <Table
              rowKey="id"
              columns={tenantColumns}
              dataSource={tenants}
              pagination={{ pageSize: 10, showSizeChanger: true }}
              scroll={{ x: 720 }}
            />
          )}
        </Card>
      </div>

      <Modal
        title={editingTenant ? '编辑租户' : '新建租户'}
        open={tenantModalOpen}
        onCancel={() => setTenantModalOpen(false)}
        onOk={() => void saveTenant()}
        confirmLoading={saving}
        destroyOnHidden
      >
        <Form form={tenantForm} layout="vertical" className="mt-4">
          <Form.Item name="name" label="租户名称" rules={[{ required: true, message: '请输入租户名称' }]}>
            <Input autoFocus placeholder="例如：示例科技" />
          </Form.Item>
          <Form.Item
            name="slug"
            label="租户标识"
            extra="只能包含小写字母、数字和连字符，创建后仍可修改。"
            rules={[
              { required: true, message: '请输入租户标识' },
              { pattern: /^[a-z0-9]+(?:-[a-z0-9]+)*$/, message: '格式不正确' },
            ]}
          >
            <Input placeholder="example-tech" />
          </Form.Item>
          {editingTenant && (
            <Form.Item name="is_active" label="启用租户" valuePropName="checked">
              <Switch />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Drawer
        title={selectedTenant ? `${selectedTenant.name} · 用户` : '租户用户'}
        open={Boolean(selectedTenant)}
        onClose={() => setSelectedTenant(null)}
        width={760}
        extra={
          <Button
            type="primary"
            icon={<UserAddOutlined />}
            disabled={!selectedTenant?.is_active}
            onClick={() => void openMemberModal()}
          >
            选择用户
          </Button>
        }
      >
        <Table
          rowKey="id"
          columns={userColumns}
          dataSource={users}
          loading={usersLoading}
          pagination={{ pageSize: 10 }}
          scroll={{ x: 620 }}
          locale={{ emptyText: '该租户暂无用户' }}
        />
      </Drawer>

      <Modal
        title={`选择用户加入 ${selectedTenant?.name ?? ''}`}
        open={memberModalOpen}
        onCancel={() => setMemberModalOpen(false)}
        onOk={() => void assignUsers()}
        okButtonProps={{ disabled: selectedUserIds.length === 0 }}
        confirmLoading={saving}
        destroyOnHidden
      >
        <div className="mt-4">
          <Text type="secondary" className="mb-2 block">
            用户账号请在“用户管理”中创建。这里仅维护租户成员关系。
          </Text>
          <Select
            mode="multiple"
            showSearch
            optionFilterProp="label"
            className="w-full"
            placeholder="搜索并选择已有用户"
            value={selectedUserIds}
            onChange={setSelectedUserIds}
            options={allUsers
              .filter((user) => !users.some((member) => member.id === user.id))
              .map((user) => ({
                value: user.id,
                label: `${user.display_name || user.username} · ${user.email}`,
              }))}
            notFoundContent="没有可添加的用户"
          />
        </div>
      </Modal>
    </div>
  );
}
