#!/bin/bash
echo $PYTHONPATH
CONTAINER_WORKSPACE_FOLDER=$1
cd $CONTAINER_WORKSPACE_FOLDER
sudo apt update -y
sudo apt install -y curl
sudo apt-get install -y docker.io

curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/nodesource_setup.sh
sudo bash /tmp/nodesource_setup.sh
sudo apt-get install -y nodejs

cd $HOME
echo $PWD
if [ ! -d ./lnbits ] ; then 
    git clone https://github.com/lnbits/lnbits.git lnbits
fi 
cd lnbits
echo $PWD
git checkout v1.0.0-rc7
poetry env use 3.12
POETRY_PYTHON_PATH=$(poetry env info -p)/bin/python
ln -sf $POETRY_PYTHON_PATH /home/vscode/python
make bundle
poetry install --no-interaction 
mkdir -p lnbits/extensions/ 
if [ ! -d lnbits/extensions/nwcprovider ] ; then 
    ln -s $CONTAINER_WORKSPACE_FOLDER lnbits/extensions/nwcprovider
fi

cd $CONTAINER_WORKSPACE_FOLDER
poetry install --no-interaction
npm i prettier
npm i pyright