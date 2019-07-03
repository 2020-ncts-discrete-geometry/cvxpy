conda config --add channels conda-forge
conda config --add channels oxfordcontrol
conda create -n testenv --yes python=$env:PYTHON_VERSION mkl=2018.0.3 pip nose numpy scipy
conda activate testenv
$text = 'python=$env:PYTHON_VERSION' | Out-File $text -FilePath C:\conda\envs\testenv\conda-meta\pinned
Get-Content -Path C:\conda\envs\testenv\conda-meta\pinned
conda install --yes lapack ecos multiprocess
pip install scs<=2.0
conda install -c anaconda --yes flake8
python setup.py install
