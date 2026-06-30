import { useEffect, useState } from 'react';
import { ClockCircleOutlined } from '@ant-design/icons';

interface Props {
  /** Timeout in seconds from creation time */
  timeoutSeconds: number;
  /** ISO timestamp when the confirmation was created */
  createdAt: string;
  /** Called when timer expires */
  onTimeout?: () => void;
}

export default function ConfirmationTimer({ timeoutSeconds, createdAt, onTimeout }: Props) {
  const [remaining, setRemaining] = useState(timeoutSeconds);

  useEffect(() => {
    const startTime = new Date(createdAt).getTime();
    const endTime = startTime + timeoutSeconds * 1000;

    const tick = () => {
      const now = Date.now();
      const secs = Math.max(0, Math.floor((endTime - now) / 1000));
      setRemaining(secs);
      if (secs === 0) {
        onTimeout?.();
      }
    };

    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [createdAt, timeoutSeconds, onTimeout]);

  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  const isUrgent = remaining <= 30;
  const isExpired = remaining === 0;

  return (
    <span
      className={`text-xs font-mono ml-auto ${
        isExpired
          ? 'text-red-500'
          : isUrgent
            ? 'text-orange-500 animate-pulse'
            : 'text-muted-foreground'
      }`}
    >
      <ClockCircleOutlined className="mr-1" />
      {isExpired ? '已超时' : `${minutes}:${seconds.toString().padStart(2, '0')}`}
    </span>
  );
}
