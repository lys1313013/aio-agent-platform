import { SaveOutlined, CheckCircleFilled } from '@ant-design/icons';
import { Button } from 'antd';


export function SaveButtonRow({
  saving,
  saved,
  onSave,
}: {
  saving: boolean;
  saved: boolean;
  onSave: () => void;
}) {
  return (
    <div className="sticky bottom-0 z-10 -mx-3 -mb-3 mt-4 flex items-center gap-2 border-t border-border/60 bg-card/95 p-3 backdrop-blur">
      <Button
        type="primary"
        size="middle"
        icon={<SaveOutlined />}
        loading={saving}
        onClick={onSave}
        className="flex-1 !rounded-lg"
      >
        保存
      </Button>
      {saved && (
        <span className="text-green-500 text-xs flex items-center gap-1">
          <CheckCircleFilled /> 已保存
        </span>
      )}
    </div>
  );
}
