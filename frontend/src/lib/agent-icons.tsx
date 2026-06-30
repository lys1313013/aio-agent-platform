import { type LucideIcon, Bot, CodeXml, Brain, Rocket, Star, BookOpen, Pen, Globe, Shield, Zap } from 'lucide-react';

const iconComponents: Record<string, LucideIcon> = {
  robot: Bot,
  code: CodeXml,
  brain: Brain,
  rocket: Rocket,
  star: Star,
  book: BookOpen,
  pen: Pen,
  globe: Globe,
  shield: Shield,
  lightning: Zap,
};

const iconLabels: Record<string, string> = {
  robot: 'Robot',
  code: 'Code',
  brain: 'Brain',
  rocket: 'Rocket',
  star: 'Star',
  book: 'Book',
  pen: 'Pen',
  globe: 'Globe',
  shield: 'Shield',
  lightning: 'Lightning',
};

export const DEFAULT_ICON = 'robot';

export function getAgentIcon(key: string, className?: string, size?: number) {
  const Icon = iconComponents[key] || iconComponents[DEFAULT_ICON];
  return <Icon className={className} size={size} />;
}

export const AGENT_ICON_OPTIONS = Object.entries(iconComponents).map(([value, Icon]) => ({
  value,
  label: iconLabels[value] || value,
  icon: Icon,
}));
