import { Link, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import { RobotOutlined } from '@ant-design/icons';
import { Form, Input, Button, Alert, Typography } from 'antd';

const { Title, Text } = Typography;

interface RegisterForm {
  username: string;
  email: string;
  password: string;
}

export default function RegisterPage() {
  const navigate = useNavigate();
  const { register, isLoading, error, clearError } = useAuthStore();
  const [form] = Form.useForm<RegisterForm>();

  const handleSubmit = async (values: RegisterForm) => {
    const ok = await register(values.username, values.email, values.password);
    if (ok) navigate('/', { replace: true });
  };

  const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    form.submit();
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-6">
        {/* Header */}
        <div className="flex flex-col items-center space-y-2">
          <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10">
            <RobotOutlined className="text-2xl text-primary" />
          </div>
          <Title level={3} className="!mb-0">
            创建账户
          </Title>
          <Text type="secondary">开始使用智能体平台</Text>
        </div>

        {/* Form */}
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          className="space-y-2"
        >
          {error && (
            <Alert
              message={error}
              type="error"
              closable
              onClose={clearError}
              showIcon
              className="mb-4"
            />
          )}

          <Form.Item
            name="username"
            label="用户名"
            rules={[
              { required: true, message: '请输入用户名' },
              { min: 3, message: '用户名至少 3 个字符' },
              { max: 64, message: '用户名最多 64 个字符' },
            ]}
          >
            <Input placeholder="alice" size="large" autoFocus />
          </Form.Item>

          <Form.Item
            name="email"
            label="邮箱"
            rules={[
              { required: true, message: '请输入邮箱' },
              { type: 'email', message: '请输入有效的邮箱地址' },
            ]}
          >
            <Input placeholder="alice@example.com" size="large" />
          </Form.Item>

          <Form.Item
            name="password"
            label="密码"
            rules={[
              { required: true, message: '请输入密码' },
              { min: 8, message: '密码至少 8 个字符' },
            ]}
          >
            <Input.Password placeholder="至少 8 个字符" size="large" />
          </Form.Item>

          <Form.Item>
            <Button
              type="primary"
              htmlType="button"
              size="large"
              loading={isLoading}
              onClick={handleClick}
              block
            >
              创建账户
            </Button>
          </Form.Item>
        </Form>

        <div className="text-center">
          <Text type="secondary">
            已有账户？{' '}
            <Link to="/login">立即登录</Link>
          </Text>
        </div>
      </div>
    </div>
  );
}
