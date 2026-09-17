-- Machine snapshot taken at init (sdk._system_info), kept apart from config so it
-- never pollutes a hyperparameter diff: {hostname, gpu_count, gpu_names, world_size,
-- rank, slurm_job_id, nodes}. world_size (torchrun/SLURM, one process per GPU) is
-- the cluster-wide GPU count the dashboard bills against; gpu_count is one node's.
ALTER TABLE runs ADD COLUMN system TEXT NOT NULL DEFAULT '{}';
