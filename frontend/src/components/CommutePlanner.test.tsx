import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import { PlacePicker } from './CommutePlanner';
import { station } from '../test/fixtures';

const options = [station(), { ...station('demo', 'Synthetic offline station'), is_demo: true }];

it('allows explicitly synthetic landmarks in the offline commute planner', () => {
  const choose = vi.fn();
  render(<PlacePicker label="Origin" stations={options} onChange={choose} />);
  fireEvent.focus(screen.getByRole('combobox'));
  fireEvent.click(screen.getByRole('option', { name: 'Synthetic offline station' }));
  expect(choose).toHaveBeenCalledWith(expect.objectContaining({ station_id: 'demo' }));
});

it('excludes synthetic stations from real-measurement alert selection', () => {
  render(<PlacePicker label="Alert station" stations={options} onChange={vi.fn()} stationOnly />);
  fireEvent.focus(screen.getByRole('combobox'));
  expect(
    screen.queryByRole('option', { name: 'Synthetic offline station' }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Alpha & First' })).toBeVisible();
});
