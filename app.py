from flask import Flask, render_template, redirect, url_for, request, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime
import os
import csv
import openpyxl
import io
import json
import unicodedata
import pandas as pd

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_aqui'

MOVIMENTACOES_FILE = 'movimentacoes.json'


# --- DECORADORES DE AUTENTICAÇÃO E PERMISSÃO ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        
        if session.get('primeiro_acesso') and request.endpoint not in ['trocar_senha_obrigatoria', 'logout', 'static']:
            flash('Sua senha precisa ser alterada no primeiro acesso/reset antes de continuar.', 'info')
            return redirect(url_for('trocar_senha_obrigatoria'))
            
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        permissao = session.get('user', {}).get('permissao')
        if permissao not in ['admin', 'administrador']:
            flash('Acesso restrito a Administradores.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function


def gestor_required(f):
    """Permite acesso para Admin e Abastecedor"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        permissao = session.get('user', {}).get('permissao')
        if permissao not in ['admin', 'administrador', 'abastecedor']:
            flash('Acesso restrito a Administradores e Abastecedores.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function


def solicitante_ou_admin_required(f):
    """Permite criar requisições apenas para Solicitante e Administrador"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        permissao = session.get('user', {}).get('permissao')
        if permissao not in ['solicitante', 'admin', 'administrador']:
            flash('Usuários do perfil Abastecedor não possuem permissão para realizar requisições.', 'warning')
            return redirect(url_for('pendentes'))
        return f(*args, **kwargs)
    return decorated_function


# --- FUNÇÕES AUXILIARES DE TEXTO ---

def normalizar_texto(texto):
    """Remove acentos, espaços extras e converte para minúsculo"""
    if not texto:
        return ""
    texto_str = str(texto).strip().lower()
    return unicodedata.normalize('NFKD', texto_str).encode('ASCII', 'ignore').decode('utf-8')


# --- FUNÇÕES DE CARREGAMENTO E SALVAMENTO DE DADOS ---

def carregar_dados(caminho_arquivo, default_data):
    os.makedirs(os.path.dirname(caminho_arquivo), exist_ok=True)
    if not os.path.exists(caminho_arquivo) or os.path.getsize(caminho_arquivo) == 0:
        with open(caminho_arquivo, 'w', encoding='utf-8') as f:
            json.dump(default_data, f)
    with open(caminho_arquivo, 'r', encoding='utf-8') as f:
        return json.load(f)


def salvar_dados(caminho_arquivo, data):
    with open(caminho_arquivo, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)


def carregar_usuarios():
    caminho = os.path.join('data', 'usuarios.json')
    if not os.path.exists(caminho):
        return []
    with open(caminho, 'r', encoding='utf-8') as f:
        return json.load(f)


def salvar_usuarios(usuarios):
    salvar_dados('data/usuarios.json', usuarios)


def carregar_materiais():
    return carregar_dados('data/materiais.json', [])


def salvar_materiais(materiais):
    salvar_dados('data/materiais.json', materiais)


def carregar_requisicoes():
    return carregar_dados('data/requisicoes.json', [])


def salvar_requisicoes(requisicoes):
    salvar_dados('data/requisicoes.json', requisicoes)


def carregar_departamentos():
    return carregar_dados('data/departamentos.json', ["Logística", "Recursos Humanos", "Financeiro", "TI", "Produção", "MAF Betim", "MAF Porto Real"])


def salvar_departamentos(departamentos):
    salvar_dados('data/departamentos.json', departamentos)


def carregar_movimentacoes():
    if not os.path.exists(MOVIMENTACOES_FILE):
        return []
    with open(MOVIMENTACOES_FILE, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def salvar_movimentacoes(movs):
    with open(MOVIMENTACOES_FILE, 'w', encoding='utf-8') as f:
        json.dump(movs, f, ensure_ascii=False, indent=4)


def registrar_movimentacao(codigo, descricao, categoria, tipo, quantidade, usuario, nota_fiscal='-', motivo='-', requisicao_id=None):
    movs = carregar_movimentacoes()
    nova_mov = {
        'id': len(movs) + 1,
        'data': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'codigo_material': str(codigo),
        'descricao_material': descricao,
        'categoria': categoria,
        'tipo': tipo,
        'quantidade': int(quantidade),
        'nota_fiscal': nota_fiscal or '-',
        'motivo': motivo or '-',
        'requisicao_id': requisicao_id,
        'usuario': usuario
    }
    movs.append(nova_mov)
    salvar_movimentacoes(movs)


# --- ROTAS PRINCIPAIS ---

@app.route('/')
def home():
    if 'user' in session:
        permissao = session['user'].get('permissao')
        if permissao in ['admin', 'administrador', 'abastecedor']:
            return redirect(url_for('pendentes'))
        else:
            return redirect(url_for('fazer_requisicao'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        senha = request.form.get('senha', '').strip()

        usuarios = carregar_usuarios()
        
        usuario_encontrado = next(
            (u for u in usuarios if str(u.get('username', '')).strip().lower() == username or str(u.get('registro', '')).strip().lower() == username), 
            None
        )

        if usuario_encontrado:
            senha_salva = usuario_encontrado.get('senha', '')
            senha_valida = False
            
            if senha_salva.startswith('scrypt:') or senha_salva.startswith('pbkdf2:'):
                senha_valida = check_password_hash(senha_salva, senha)
            else:
                senha_valida = (senha_salva == senha)

            if senha_valida:
                session['user'] = {
                    'username': usuario_encontrado.get('username'),
                    'registro': usuario_encontrado.get('registro'),
                    'nome': usuario_encontrado.get('nome'),
                    'permissao': usuario_encontrado.get('permissao') or usuario_encontrado.get('perfil'),
                    'departamento': usuario_encontrado.get('departamento', 'Geral')
                }
                
                session['primeiro_acesso'] = usuario_encontrado.get('primeiro_acesso', False)

                flash('Login realizado com sucesso!', 'success')

                if session.get('primeiro_acesso'):
                    return redirect(url_for('trocar_senha_obrigatoria'))

                return redirect(url_for('home'))

        flash('Usuário ou senha incorretos. Tente novamente.', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/trocar_senha_obrigatoria', methods=['GET', 'POST'])
def trocar_senha_obrigatoria():
    if 'user' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        nova_senha = request.form.get('nova_senha')
        confirmacao = request.form.get('confirmacao_senha')

        if nova_senha != confirmacao:
            flash('As senhas não coincidem. Tente novamente.', 'danger')
            return render_template('trocar_senha_obrigatoria.html')

        usuarios = carregar_usuarios()
        registro_logado = str(session['user']['registro']).strip()

        for u in usuarios:
            reg_u = str(u.get('registro') or u.get('username', '')).strip()
            if reg_u == registro_logado:
                u['senha'] = generate_password_hash(nova_senha)
                u['primeiro_acesso'] = False
                break
        
        salvar_usuarios(usuarios)
        session['primeiro_acesso'] = False
        flash('Senha alterada com sucesso! Bem-vindo ao sistema.', 'success')
        return redirect(url_for('home'))

    return render_template('trocar_senha_obrigatoria.html')


@app.route('/trocar_senha', methods=['GET', 'POST'])
@login_required
def trocar_senha():
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual', '').strip()
        nova_senha = request.form.get('nova_senha', '').strip()
        confirmacao = request.form.get('confirmacao_senha', '').strip()

        if nova_senha != confirmacao:
            flash('A nova senha e a confirmação não coincidem.', 'danger')
            return render_template('trocar_senha.html')

        usuarios = carregar_usuarios()
        registro_logado = str(session['user']['registro']).strip()

        for u in usuarios:
            reg_u = str(u.get('registro') or u.get('username', '')).strip()
            if reg_u == registro_logado:
                senha_salva = u.get('senha', '')
                senha_valida = False
                
                if senha_salva.startswith('scrypt:') or senha_salva.startswith('pbkdf2:'):
                    senha_valida = check_password_hash(senha_salva, senha_atual)
                else:
                    senha_valida = (senha_salva == senha_atual)

                if not senha_valida:
                    flash('Senha atual incorreta.', 'danger')
                    return render_template('trocar_senha.html')

                u['senha'] = generate_password_hash(nova_senha)
                u['primeiro_acesso'] = False
                salvar_usuarios(usuarios)
                flash('Sua senha foi alterada com sucesso!', 'success')
                return redirect(url_for('home'))

        flash('Usuário não encontrado.', 'danger')

    return render_template('trocar_senha.html')


@app.route('/fazer_requisicao')
@login_required
@solicitante_ou_admin_required
def fazer_requisicao():
    materiais = carregar_materiais()
    departamentos = carregar_departamentos()
    
    usuario_logado = session['user']
    registro_logado = str(usuario_logado.get('registro')).strip()
    permissao_usuario = usuario_logado.get('permissao', '')
    
    usuarios = carregar_usuarios()
    dados_usuario = next((u for u in usuarios if str(u.get('registro') or u.get('username', '')).strip() == registro_logado), {})
    
    pode_acessar_epi = (
        permissao_usuario in ['admin', 'administrador'] or 
        dados_usuario.get('pode_solicitar_epi', False) is True
    )
    
    categoria_atual = request.args.get('categoria', 'escritorio')
    
    if categoria_atual == 'epi' and not pode_acessar_epi:
        flash('Seu usuário não possui permissão para solicitar EPIs. Contate o administrador.', 'warning')
        return redirect(url_for('fazer_requisicao', categoria='escritorio'))
    
    materiais_filtrados = [
        m for m in materiais 
        if m.get('categoria', 'escritorio') == categoria_atual
    ]
    
    return render_template(
        'fazer_requisicao.html', 
        materiais=materiais_filtrados, 
        departamentos=departamentos,
        categoria_atual=categoria_atual,
        pode_acessar_epi=pode_acessar_epi
    )


@app.route('/enviar_requisicao', methods=['POST'])
@login_required
@solicitante_ou_admin_required
def enviar_requisicao():
    materiais_disponiveis = carregar_materiais()
    itens_requisicao = []

    categoria_solicitacao = request.form.get('categoria_solicitacao', 'escritorio')
    
    # CAPTURA O TIPO DA REQUISIÇÃO: 'devolucao' ou 'saida' (padrão)
    tipo_requisicao = request.form.get('tipo_requisicao', 'saida')

    for key, value in request.form.items():
        if key.startswith('materiais[') and key.endswith('][codigo]'):
            index = key.split('[')[1].split(']')[0]
            codigo_material = value
            quantidade = request.form.get(f'materiais[{index}][quantidade]')

            if not codigo_material or not quantidade:
                continue

            quantidade = int(quantidade)
            if quantidade <= 0:
                continue

            material_encontrado = next(
                (
                    m for m in materiais_disponiveis 
                    if str(m['codigo']) == str(codigo_material) 
                    and m.get('categoria', categoria_solicitacao) == categoria_solicitacao
                ), 
                None
            )
            
            if not material_encontrado:
                flash(f'Material com código {codigo_material} não encontrado.', 'danger')
                return redirect(url_for('fazer_devolucao' if tipo_requisicao == 'devolucao' else 'fazer_requisicao'))

            itens_requisicao.append({
                'codigo': codigo_material,
                'nome': material_encontrado['descricao'],
                'quantidade': quantidade,
                'quantidade_solicitada': quantidade,
                'quantidade_separada': quantidade
            })

    if not itens_requisicao:
        flash('Nenhum item válido foi adicionado à requisição.', 'warning')
        return redirect(url_for('fazer_devolucao' if tipo_requisicao == 'devolucao' else 'fazer_requisicao'))

    requisicoes = carregar_requisicoes()
    usuario_logado = session['user']
    
    # ID sequencial auto-incrementado
    max_id = max([req.get('id', 0) for req in requisicoes], default=0)
    novo_id = max_id + 1

    nova_requisicao = {
        'id': novo_id,
        'nome': usuario_logado.get('nome'),
        'username': usuario_logado.get('username'),
        'registro': usuario_logado.get('registro'),
        'departamento': usuario_logado.get('departamento'),
        'categoria': categoria_solicitacao,
        'tipo_requisicao': tipo_requisicao,  # <--- CRUCIAL: SALVA O TIPO DA REQUISIÇÃO
        'data': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'status': 'pendente',
        'nota_fiscal': '-',
        'itens': itens_requisicao
    }

    requisicoes.append(nova_requisicao)
    salvar_requisicoes(requisicoes)
    
    if tipo_requisicao == 'devolucao':
        flash(f'Devolução #{novo_id} enviada com sucesso para conferência!', 'success')
        return redirect(url_for('fazer_devolucao'))
    else:
        flash(f'Requisição #{novo_id} enviada com sucesso!', 'success')
        return redirect(url_for('fazer_requisicao', categoria=categoria_solicitacao))
    
@app.route('/pendentes')
@login_required
@gestor_required
def pendentes():
    todas_requisicoes = carregar_requisicoes()
    
    # Apenas SAÍDAS entram aqui
    requisicoes_saida = [
        r for r in todas_requisicoes 
        if r.get('status') == 'pendente' and r.get('tipo_requisicao', 'saida') != 'devolucao'
    ]
    
    return render_template('pendentes.html', requisicoes=requisicoes_saida)


@app.route('/devolucoes_pendentes')
@login_required
@gestor_required
def devolucoes_pendentes():
    todas_requisicoes = carregar_requisicoes()
    
    # Apenas DEVOLUÇÕES entram aqui
    devolucoes = [
        r for r in todas_requisicoes 
        if r.get('status') == 'pendente' and r.get('tipo_requisicao') == 'devolucao'
    ]
    
    return render_template('devolucoes_pendentes.html', requisicoes=devolucoes)
@app.route('/separados')
@login_required
@gestor_required
def separados():
    requisicoes = carregar_requisicoes()
    separados_list = [r for r in requisicoes if r.get('status') == 'separado']
    return render_template('separados.html', requisicoes=separados_list)


@app.route('/concluir_requisicao', methods=['POST'])
@login_required
@gestor_required
def concluir_requisicao():
    requisicao_id_raw = request.form.get('requisicao_id')
    if not requisicao_id_raw:
        flash('ID de requisição inválido.', 'danger')
        return redirect(url_for('pendentes'))
        
    requisicao_id = int(requisicao_id_raw)
    requisicoes = carregar_requisicoes()
    materiais_disponiveis = carregar_materiais()
    
    requisicao = next((r for r in requisicoes if r['id'] == requisicao_id), None)
    if not requisicao:
        flash('Requisição não encontrada.', 'danger')
        return redirect(url_for('pendentes'))

    tipo_req = requisicao.get('tipo_requisicao', 'saida')
    categoria_req = requisicao.get('categoria', 'escritorio')
    depto_solicitante = str(requisicao.get('departamento', '')).strip().lower()
    eh_maf = ('maf porto real' in depto_solicitante) or ('maf betim' in depto_solicitante)
    acao_final = request.form.get('acao_final')

    # =========================================================================
    # FLUXO DIRETO PARA DEVOLUÇÕES (Pendente -> Concluída de uma só vez)
    # =========================================================================
    if tipo_req == 'devolucao':
        for item in requisicao.get('itens', []):
            codigo_item = str(item.get('codigo', ''))
            nova_qtd_str = request.form.get(f'qtd_item_{codigo_item}')
            
            try:
                qtd_devolver = int(nova_qtd_str) if nova_qtd_str is not None else item.get('quantidade', 0)
            except ValueError:
                qtd_devolver = item.get('quantidade', 0)

            qtd_devolver = max(0, qtd_devolver)
            item['quantidade_solicitada'] = item.get('quantidade_solicitada', item.get('quantidade', 0))
            item['quantidade_separada'] = qtd_devolver

            if qtd_devolver > 0:
                # 1. Atualiza o saldo somando na Posição do Estoque
                mat_estoque = next((m for m in materiais_disponiveis if str(m['codigo']) == codigo_item), None)
                if mat_estoque:
                    mat_estoque['saldo'] = mat_estoque.get('saldo', 0) + qtd_devolver

                # 2. Registra o histórico de movimentação como positivo (Entrada / Devolução)
                registrar_movimentacao(
                    codigo=item.get('codigo', '-'),
                    descricao=item.get('nome') or item.get('descricao', '-'),
                    categoria=categoria_req,
                    tipo='Entrada',  # Registra como Entrada para marcar +Qtd no histórico
                    quantidade=qtd_devolver,
                    usuario=session['user'].get('nome', 'Abastecedor'),
                    nota_fiscal='-',
                    motivo=f"Entrada por Devolução da Requisição #{requisicao['id']}",
                    requisicao_id=requisicao['id']
                )

        # 3. Conclui e encerra a devolução diretamente
        usuario_acao = session['user'].get('nome', 'Abastecedor')
        requisicao['status'] = 'concluida'
        requisicao['data_retirada'] = datetime.now().strftime('%d/%m/%Y %H:%M')
        requisicao['data_conclusao'] = datetime.now().strftime('%d/%m/%Y %H:%M')
        requisicao['separado_por'] = usuario_acao
        requisicao['finalizado_por'] = usuario_acao

        salvar_requisicoes(requisicoes)
        salvar_materiais(materiais_disponiveis)

        flash(f'Devolução #{requisicao_id} aceita! O saldo foi incrementado no estoque.', 'success')
        return redirect(url_for('devolucoes_pendentes'))

    # =========================================================================
    # FLUXO DE SAÍDA PADRÃO (Separação em 2 Etapas: Pendente -> Separado -> Concluída)
    # =========================================================================
    if requisicao.get('status') == 'pendente' or acao_final == 'separar':
        itens_para_processar = []

        for item in requisicao.get('itens', []):
            codigo_item = str(item.get('codigo', ''))
            nova_qtd_str = request.form.get(f'qtd_item_{codigo_item}')
            
            try:
                qtd_separar = int(nova_qtd_str) if nova_qtd_str is not None else item.get('quantidade', 0)
            except ValueError:
                qtd_separar = item.get('quantidade', 0)

            qtd_separar = max(0, qtd_separar)

            if categoria_req == 'escritorio':
                mat_estoque = next((m for m in materiais_disponiveis if str(m['codigo']) == codigo_item), None)
                if not mat_estoque:
                    flash(f"Material {item.get('nome')} não cadastrado no estoque.", 'danger')
                    return redirect(url_for('ver_requisicao', requisicao_id=requisicao_id))

                saldo_atual = mat_estoque.get('saldo', 0)

                if qtd_separar > saldo_atual:
                    flash(
                        f"Saldo insuficiente para '{item.get('nome')}'. "
                        f"Solicitado: {qtd_separar} un | Saldo em Estoque: {saldo_atual} un.", 
                        'danger'
                    )
                    return redirect(url_for('ver_requisicao', requisicao_id=requisicao_id))

                itens_para_processar.append({
                    'item_ref': item,
                    'mat_estoque': mat_estoque,
                    'qtd_separar': qtd_separar
                })
            else:
                item['quantidade_solicitada'] = item.get('quantidade_solicitada', item.get('quantidade', 0))
                item['quantidade_separada'] = qtd_separar

                if qtd_separar > 0:
                    registrar_movimentacao(
                        codigo=item.get('codigo', '-'),
                        descricao=item.get('nome') or item.get('descricao', '-'),
                        categoria='epi',
                        tipo='Consumo Requisição',
                        quantidade=qtd_separar,
                        usuario=session['user'].get('nome', 'Abastecedor'),
                        nota_fiscal=requisicao.get('nota_fiscal', '-'),
                        motivo=f"Separação da Requisição #{requisicao['id']}",
                        requisicao_id=requisicao['id']
                    )

        if categoria_req == 'escritorio':
            for proc in itens_para_processar:
                item = proc['item_ref']
                mat_estoque = proc['mat_estoque']
                qtd_separada = proc['qtd_separar']

                item['quantidade_solicitada'] = item.get('quantidade_solicitada', item.get('quantidade', 0))
                item['quantidade_separada'] = qtd_separada

                if qtd_separada > 0:
                    mat_estoque['saldo'] = mat_estoque.get('saldo', 0) - qtd_separada
                    
                    registrar_movimentacao(
                        codigo=mat_estoque['codigo'],
                        descricao=mat_estoque['descricao'],
                        categoria='escritorio',
                        tipo='Consumo Requisição',
                        quantidade=qtd_separada,
                        usuario=session['user'].get('nome', 'Abastecedor'),
                        nota_fiscal=requisicao.get('nota_fiscal', '-'),
                        motivo=f"Separação da Requisição #{requisicao['id']}",
                        requisicao_id=requisicao['id']
                    )

        requisicao['status'] = 'separado'
        requisicao['data_retirada'] = datetime.now().strftime('%d/%m/%Y %H:%M')
        requisicao['separado_por'] = session['user'].get('nome', 'Abastecedor')
        
        salvar_requisicoes(requisicoes)
        salvar_materiais(materiais_disponiveis)
        
        flash(f'Requisição #{requisicao_id} separada com sucesso!', 'success')
        return redirect(url_for('pendentes'))

    elif requisicao.get('status') == 'separado' or acao_final in ['concluir', 'concluir_direto']:
        if eh_maf or acao_final == 'concluir_direto':
            nf_form = request.form.get('nota_fiscal', '').strip()
            if nf_form == '-':
                nf_form = ''

            if not nf_form and not requisicao.get('nota_fiscal'):
                flash('A Nota Fiscal é obrigatória para o despacho.', 'danger')
                return redirect(url_for('separados'))

            nota_fiscal_final = nf_form or requisicao.get('nota_fiscal') or '-'
            requisicao['nota_fiscal'] = nota_fiscal_final
            requisicao['nota_fiscal_transferencia'] = nota_fiscal_final
            usuario_conclusao = session['user'].get('nome', 'Abastecedor')

        else:
            auth_user = request.form.get('auth_usuario', '').strip().lower()
            auth_senha = request.form.get('auth_senha', '').strip()

            if not auth_user or not auth_senha:
                flash('Informe o usuário e a senha do solicitante para autenticar a entrega.', 'danger')
                return redirect(url_for('separados'))

            usuarios = carregar_usuarios()
            usuario_valido = next(
                (u for u in usuarios if str(u.get('username', '')).strip().lower() == auth_user),
                None
            )

            if not usuario_valido:
                flash('Usuário solicitante não encontrado.', 'danger')
                return redirect(url_for('separados'))

            senha_salva = usuario_valido.get('senha', '')
            senha_correta = check_password_hash(senha_salva, auth_senha) if (senha_salva.startswith('scrypt:') or senha_salva.startswith('pbkdf2:')) else (senha_salva == auth_senha)

            if not senha_correta:
                flash('Senha do solicitante incorreta. Despacho cancelado.', 'danger')
                return redirect(url_for('separados'))

            usuario_conclusao = usuario_valido.get('nome', '')

        requisicao['status'] = 'concluida'
        requisicao['data_conclusao'] = datetime.now().strftime('%d/%m/%Y %H:%M')
        requisicao['finalizado_por'] = usuario_conclusao
        
        movimentacoes = carregar_movimentacoes()
        nf_atualizada = requisicao.get('nota_fiscal', '-')
        
        houve_atualizacao = False
        for mov in movimentacoes:
            req_vinculada = mov.get('requisicao_id') or mov.get('req_id')
            if str(req_vinculada) == str(requisicao_id):
                mov['nota_fiscal'] = nf_atualizada
                houve_atualizacao = True
        
        if houve_atualizacao:
            salvar_movimentacoes(movimentacoes)

        salvar_requisicoes(requisicoes)
        flash(f'Requisição #{requisicao_id} concluída com sucesso por {usuario_conclusao}!', 'success')
        return redirect(url_for('separados'))

    return redirect(url_for('historico'))

@app.route('/devolucao')
@login_required
def fazer_devolucao():
    # Carrega os materiais da categoria escritório
    materiais_todos = carregar_materiais()
    materiais_escritorio = [
        m for m in materiais_todos 
        if str(m.get('categoria', 'escritorio')).lower() == 'escritorio'
    ]
    
    return render_template('devolucao.html', materiais=materiais_escritorio)

@app.route('/estoque')
@login_required
def ver_estoque():
    materiais = carregar_materiais()
    
    # 1. Filtra apenas os materiais de Escritório para a Posição de Estoque
    materiais_escritorio = [
        m for m in materiais 
        if m.get('categoria', 'escritorio') == 'escritorio'
    ]
    
    for m in materiais_escritorio:
        if 'saldo' not in m:
            m['saldo'] = 0
            
    return render_template('estoque.html', materiais=materiais_escritorio)


@app.route('/movimentar_estoque', methods=['GET', 'POST'])
@login_required
@gestor_required
def movimentar_estoque():
    materiais = carregar_materiais()
    # Filtra apenas materiais de escritório para movimentação manual de saldo
    materiais_escritorio = [
        m for m in materiais 
        if m.get('categoria', 'escritorio') == 'escritorio'
    ]
    
    if request.method == 'POST':
        codigo_material = str(request.form.get('codigo_material', '')).strip()
        tipo_movimentacao = request.form.get('tipo_movimentacao')
        quantidade = int(request.form.get('quantidade', 0))
        nota_fiscal = request.form.get('nota_fiscal', '').strip()
        motivo = request.form.get('motivo', '').strip()
        usuario_nome = session['user'].get('nome', 'Sistema')

        if quantidade <= 0:
            flash('A quantidade deve ser maior que zero.', 'danger')
            return redirect(url_for('movimentar_estoque'))

        material = next((m for m in materiais if str(m['codigo']) == codigo_material), None)

        if not material:
            flash('Material não encontrado no cadastro!', 'danger')
            return redirect(url_for('movimentar_estoque'))

        saldo_atual = material.get('saldo', 0)

        if tipo_movimentacao in ['entrada_nf', 'entrada_manual']:
            material['saldo'] = saldo_atual + quantidade
            tipo_txt = 'Entrada por Nota Fiscal' if tipo_movimentacao == 'entrada_nf' else 'Entrada Manual'
            
            salvar_materiais(materiais)
            registrar_movimentacao(
                codigo=material['codigo'],
                descricao=material['descricao'],
                categoria=material.get('categoria', 'escritorio'),
                tipo=tipo_txt,
                quantidade=quantidade,
                usuario=usuario_nome,
                nota_fiscal=nota_fiscal,
                motivo=motivo
            )
            flash(f"Entrada de {quantidade} un. em '{material['descricao']}' realizada com sucesso!", 'success')

        elif tipo_movimentacao in ['saida_nf', 'saida_manual']:
            if quantidade > saldo_atual:
                flash(f"Saldo insuficiente em estoque! Saldo atual: {saldo_atual}", 'danger')
                return redirect(url_for('movimentar_estoque'))

            material['saldo'] = saldo_atual - quantidade
            tipo_txt = 'Saída por Nota Fiscal' if tipo_movimentacao == 'saida_nf' else 'Saída Manual'

            salvar_materiais(materiais)
            registrar_movimentacao(
                codigo=material['codigo'],
                descricao=material['descricao'],
                categoria=material.get('categoria', 'escritorio'),
                tipo=tipo_txt,
                quantidade=quantidade,
                usuario=usuario_nome,
                nota_fiscal=nota_fiscal,
                motivo=motivo
            )
            flash(f"Saída de {quantidade} un. de '{material['descricao']}' realizada com sucesso!", 'warning')

        return redirect(url_for('ver_estoque'))

    return render_template('movimentar_estoque.html', materiais=materiais_escritorio)


@app.route('/historico_movimentacoes')
@login_required
@gestor_required
def historico_movimentacoes():
    # Carrega o histórico de movimentações (movimentacoes.json ou banco)
    movimentacoes = carregar_movimentacoes()
    
    # Ordena das mais recentes para as mais antigas
    movimentacoes = sorted(movimentacoes, key=lambda x: x.get('data', ''), reverse=True)
    
    return render_template('historico_movimentacoes.html', movimentacoes=movimentacoes)

@app.route('/api/checar_estoque/<codigo>')
@login_required
def checar_estoque_api(codigo):
    materiais = carregar_materiais()
    mat = next((m for m in materiais if str(m['codigo']) == str(codigo)), None)
    if mat:
        return {'sucesso': True, 'saldo': mat.get('saldo', 0), 'descricao': mat['descricao']}
    return {'sucesso': False, 'saldo': 0}


# --- HISTÓRICO E EXPORTAÇÃO ---

@app.route('/historico')
@login_required
def historico():
    requisicoes = carregar_requisicoes()
    permissao = session['user'].get('permissao')
    
    if permissao in ['admin', 'administrador', 'abastecedor']:
        requisicoes_a_exibir = requisicoes
    else:
        registro_usuario = str(session['user'].get('registro', '')).strip()
        requisicoes_a_exibir = [
            req for req in requisicoes
            if str(req.get('registro', '')).strip() == registro_usuario
        ]

    requisicoes_ordenadas = requisicoes_a_exibir[::-1]
    return render_template('historico.html', requisicoes=requisicoes_ordenadas)


@app.route('/excluir_requisicao/<int:requisicao_id>', methods=['POST'])
@login_required
def excluir_requisicao(requisicao_id):
    requisicoes = carregar_requisicoes()
    requisicao_para_excluir = next((req for req in requisicoes if req['id'] == requisicao_id), None)

    if not requisicao_para_excluir:
        flash('Requisição não encontrada.', 'danger')
        return redirect(url_for('historico'))

    usuario_logado = session['user']
    registro_logado = str(usuario_logado.get('registro', '')).strip()
    permissao_logada = usuario_logado.get('permissao', '')

    def efetuar_exclusao_e_limpar_historico():
        # 1. Remove a requisição da lista
        requisicoes.remove(requisicao_para_excluir)
        salvar_requisicoes(requisicoes)

        # 2. Busca e remove todas as movimentações atreladas a este ID de requisição
        movimentacoes = carregar_movimentacoes()
        movimentacoes_filtradas = [
            m for m in movimentacoes 
            if str(m.get('requisicao_id')) != str(requisicao_id)
        ]
        salvar_movimentacoes(movimentacoes_filtradas)

    if permissao_logada in ['admin', 'administrador']:
        efetuar_exclusao_e_limpar_historico()
        flash(f'Requisição #{requisicao_id} e seus históricos de movimentação foram excluídos com sucesso.', 'success')

    elif permissao_logada == 'abastecedor':
        flash('Usuários do perfil Abastecedor não têm permissão para excluir requisições.', 'danger')

    else:
        registro_pedido = str(requisicao_para_excluir.get('registro', '')).strip()
        status_pedido = requisicao_para_excluir.get('status')

        if registro_pedido == registro_logado:
            if status_pedido == 'pendente':
                efetuar_exclusao_e_limpar_historico()
                flash(f'Sua requisição #{requisicao_id} foi excluída com sucesso!', 'success')
            else:
                flash('Não é possível excluir uma requisição que já foi separada ou concluída.', 'warning')
        else:
            flash('Você não tem permissão para excluir a requisição de outro usuário.', 'danger')

    return redirect(request.referrer or url_for('historico'))


@app.route('/exportar_relatorio_excel')
@login_required
@gestor_required
def exportar_relatorio_excel():
    requisicoes = carregar_requisicoes()
    
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    
    data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d') if data_inicio_str else None
    data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d') if data_fim_str else None

    linhas_relatorio = []

    for req in requisicoes:
        data_req_raw = req.get('data', '')
        try:
            if '/' in data_req_raw:
                data_req_dt = datetime.strptime(data_req_raw.split(' ')[0], '%d/%m/%Y')
            else:
                data_req_dt = datetime.strptime(data_req_raw.split(' ')[0], '%Y-%m-%d')
        except Exception:
            data_req_dt = None

        if data_inicio and data_req_dt and data_req_dt < data_inicio:
            continue
        if data_fim and data_req_dt and data_req_dt > data_fim:
            continue

        itens = req.get('itens', [])
        
        status_traduzido = req.get('status', '').capitalize()
        if status_traduzido.lower() == 'concluida':
            status_traduzido = 'Concluída'

        categoria_traduzida = 'EPI' if req.get('categoria') == 'epi' else 'Escritório'

        if itens:
            for item in itens:
                linhas_relatorio.append({
                    'ID Requisição': req.get('id'),
                    'Data Solicitação': req.get('data'),
                    'Status': status_traduzido,
                    'Tipo/Categoria': categoria_traduzida,
                    'Nota Fiscal': req.get('nota_fiscal', '-'),
                    'Solicitante': req.get('nome'),
                    'Registro': req.get('registro'),
                    'Departamento': req.get('departamento'),
                    'Código Material': item.get('codigo', '-'),
                    'Descrição Material': item.get('nome') or item.get('descricao', '-'),
                    'Qtd Solicitada': item.get('quantidade_solicitada', item.get('quantidade', 0)),
                    'Qtd Separada': item.get('quantidade_separada', item.get('quantidade', 0)),
                    'Separado Por': req.get('separado_por', '-'),
                    'Finalizado Por': req.get('finalizado_por', '-'),
                    'Data Conclusão/Retirada': req.get('data_conclusao') or req.get('data_retirada') or '-'
                })
        else:
            linhas_relatorio.append({
                'ID Requisição': req.get('id'),
                'Data Solicitação': req.get('data'),
                'Status': status_traduzido,
                'Tipo/Categoria': categoria_traduzida,
                'Nota Fiscal': req.get('nota_fiscal', '-'),
                'Solicitante': req.get('nome'),
                'Registro': req.get('registro'),
                'Departamento': req.get('departamento'),
                'Código Material': '-',
                'Descrição Material': 'Sem itens registrados',
                'Qtd Solicitada': 0,
                'Qtd Separada': 0,
                'Separado Por': req.get('separado_por', '-'),
                'Finalizado Por': req.get('finalizado_por', '-'),
                'Data Conclusão/Retirada': req.get('data_conclusao') or req.get('data_retirada') or '-'
            })

    if not linhas_relatorio:
        flash('Nenhuma requisição encontrada para o período selecionado.', 'warning')
        return redirect(url_for('historico'))

    df = pd.DataFrame(linhas_relatorio)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Histórico CoreStock')
    
    output.seek(0)
    nome_arquivo = f"Relatorio_CoreStock_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=nome_arquivo
    )

@app.route('/exportar_movimentacoes_excel', methods=['POST', 'GET'])
@login_required
@gestor_required
def exportar_movimentacoes_excel():
    movimentacoes = carregar_movimentacoes()

    data_inicio_str = request.form.get('data_inicio') or request.args.get('data_inicio')
    data_fim_str = request.form.get('data_fim') or request.args.get('data_fim')

    data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d') if data_inicio_str else None
    data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d') if data_fim_str else None

    linhas_movimentacoes = []

    for mov in movimentacoes:
        data_mov_raw = mov.get('data', '')
        try:
            if '/' in data_mov_raw:
                data_mov_dt = datetime.strptime(data_mov_raw.split(' ')[0], '%d/%m/%Y')
            else:
                data_mov_dt = datetime.strptime(data_mov_raw.split(' ')[0], '%Y-%m-%d')
        except Exception:
            data_mov_dt = None

        if data_inicio and data_mov_dt and data_mov_dt < data_inicio:
            continue
        if data_fim and data_mov_dt and data_mov_dt > data_fim:
            continue

        req_id = mov.get('requisicao_id') or mov.get('req_id') or '-'
        if str(req_id) != '-':
            req_id = f"#{req_id}"

        categoria_traduzida = 'EPI' if str(mov.get('categoria', '')).lower() == 'epi' else 'Escritório'

        linhas_movimentacoes.append({
            'Data / Hora': mov.get('data', '-'),
            'Código Material': mov.get('codigo_material') or mov.get('codigo') or '-',
            'Descrição Material': mov.get('descricao_material') or mov.get('descricao') or '-',
            'Tipo/Categoria': categoria_traduzida,
            'Tipo Movimentação': mov.get('tipo', '-'),
            'Quantidade': mov.get('quantidade', 0),
            'Nota Fiscal': mov.get('nota_fiscal', '-'),
            'Nº Requisição': req_id,
            'Usuário': mov.get('usuario', '-'),
            'Motivo / Observação': mov.get('motivo', '-')
        })

    if not linhas_movimentacoes:
        flash('Nenhuma movimentação encontrada para o período selecionado.', 'warning')
        return redirect(url_for('historico_movimentacoes'))

    df = pd.DataFrame(linhas_movimentacoes)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Movimentações de Estoque')

    output.seek(0)
    nome_arquivo = f"Relatorio_Movimentacoes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=nome_arquivo
    )

# --- GERENCIAMENTO DE USUÁRIOS, MATERIAIS E DEPARTAMENTOS ---

@app.route('/gerenciar_usuarios', methods=['GET', 'POST'])
@login_required
@admin_required
def gerenciar_usuarios():
    usuarios = carregar_usuarios()
    deps_raw = carregar_departamentos()
    
    departamentos = []
    for d in deps_raw:
        if isinstance(d, dict):
            departamentos.append(d)
        else:
            departamentos.append({'nome': str(d)})
    
    if request.method == 'POST':
        if 'acao_editar_epi' in request.form:
            reg_target = str(request.form.get('registro_target')).strip()
            status_epi = request.form.get('pode_solicitar_epi') == 'true'
            
            for u in usuarios:
                reg_u = str(u.get('registro') or u.get('username', '')).strip()
                if reg_u == reg_target:
                    u['pode_solicitar_epi'] = status_epi
                    break
                    
            salvar_usuarios(usuarios)
            flash('Permissão de EPI atualizada com sucesso!', 'success')
            return redirect(url_for('gerenciar_usuarios'))

        username = str(request.form.get('username', '')).strip().lower()
        registro = str(request.form.get('registro', '')).strip()
        nome = request.form.get('nome', '').strip()
        centro_custo = request.form.get('centro_custo', '').strip()
        departamento = request.form.get('departamento')
        permissao = request.form.get('permissao')
        pode_solicitar_epi = 'pode_solicitar_epi' in request.form
        senha_inicial = "Mudar123@"

        if any(str(u.get('username', '')).strip().lower() == username for u in usuarios):
            flash('Nome de usuário já cadastrado! Escolha outro.', 'danger')
        elif any(str(u.get('registro', '')).strip() == registro for u in usuarios):
            flash('Registro (ID) já cadastrado!', 'danger')
        else:
            usuarios.append({
                "username": username,
                "registro": registro,
                "nome": nome,
                "centro_custo": centro_custo,
                "departamento": departamento,
                "permissao": permissao,
                "perfil": permissao,
                "pode_solicitar_epi": pode_solicitar_epi,
                "senha": senha_inicial,
                "primeiro_acesso": True
            })
            salvar_usuarios(usuarios)
            flash(f'Usuário {nome} (@{username}) criado com sucesso!', 'success')
            return redirect(url_for('gerenciar_usuarios'))

    return render_template('gerenciar_usuarios.html', usuarios=usuarios, departamentos=departamentos)


@app.route('/resetar_senha/<registro>', methods=['POST'])
@login_required
@admin_required
def resetar_senha(registro):
    usuarios = carregar_usuarios()
    senha_padrao = "Mudar123@"
    
    for u in usuarios:
        reg_u = str(u.get('registro') or u.get('username', '')).strip()
        if reg_u == str(registro).strip():
            u['senha'] = senha_padrao
            u['primeiro_acesso'] = True
            break
            
    salvar_usuarios(usuarios)
    flash(f'A senha do usuário {registro} foi resetada para "{senha_padrao}".', 'success')
    return redirect(url_for('gerenciar_usuarios'))


@app.route('/excluir_usuario', methods=['POST'])
@login_required
@admin_required
def excluir_usuario():
    registro_usuario_excluir = str(request.form.get('registro_usuario')).strip()
    registro_usuario_logado = str(session['user'].get('registro')).strip()

    if registro_usuario_excluir == registro_usuario_logado:
        flash('Você não pode excluir a si mesmo.', 'warning')
        return redirect(url_for('gerenciar_usuarios'))

    usuarios = carregar_usuarios()
    usuarios_atualizados = [
        u for u in usuarios 
        if str(u.get('registro') or u.get('username', '')).strip() != registro_usuario_excluir
    ]
    salvar_usuarios(usuarios_atualizados)
    
    flash('Usuário excluído com sucesso.', 'success')
    return redirect(url_for('gerenciar_usuarios'))


@app.route('/gerenciar_materiais_escritorio', methods=['GET', 'POST'])
@login_required
@gestor_required
def gerenciar_materiais_escritorio():
    if request.method == 'POST':
        codigo = request.form.get('codigo', '').strip()
        descricao = request.form.get('descricao', '').strip()
        estoque_minimo = request.form.get('estoque_minimo', '0')

        try:
            est_min = int(estoque_minimo)
        except ValueError:
            est_min = 0

        materiais = carregar_materiais()
        
        if any(str(m['codigo']).strip() == codigo for m in materiais if m.get('categoria', 'escritorio') == 'escritorio'):
            flash('Código de material de escritório já cadastrado!', 'danger')
            return redirect(url_for('gerenciar_materiais_escritorio'))

        novo_material = {
            "codigo": codigo,
            "descricao": descricao,
            "estoque_minimo": est_min,
            "saldo": 0,
            "categoria": "escritorio"
        }
        materiais.append(novo_material)
        salvar_materiais(materiais)
        flash('Material de escritório adicionado com sucesso!', 'success')
        return redirect(url_for('gerenciar_materiais_escritorio'))
        
    materiais = carregar_materiais()
    materiais_escritorio = [m for m in materiais if m.get('categoria', 'escritorio') == 'escritorio']
    return render_template('gerenciar_materiais_escritorio.html', materiais=materiais_escritorio)
@app.route('/gerenciar_materiais_epi', methods=['GET', 'POST'])
@login_required
@gestor_required
def gerenciar_materiais_epi():
    if request.method == 'POST':
        codigo = request.form.get('codigo')
        descricao = request.form.get('descricao')
        quantidade_maxima = request.form.get('quantidade_maxima')

        materiais = carregar_materiais()
        novo_material = {
            "codigo": codigo,
            "descricao": descricao,
            "quantidade_maxima": int(quantidade_maxima),
            "categoria": "epi"
        }
        materiais.append(novo_material)
        salvar_materiais(materiais)
        flash('EPI adicionado com sucesso!', 'success')
        return redirect(url_for('gerenciar_materiais_epi'))
        
    materiais = carregar_materiais()
    materiais_epi = [m for m in materiais if m.get('categoria') == 'epi']
    return render_template('gerenciar_materiais_epi.html', materiais=materiais_epi)


@app.route('/excluir_material', methods=['POST'])
@login_required
@gestor_required
def excluir_material():
    codigo_material_excluir = request.form.get('codigo_material')
    materiais = carregar_materiais()
    materiais_atualizados = [m for m in materiais if m['codigo'] != codigo_material_excluir]
    
    salvar_materiais(materiais_atualizados)
    flash('Material excluído com sucesso!', 'success')
    return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))


@app.route('/editar_material/<codigo_material>', methods=['GET', 'POST'])
@login_required
@gestor_required
def editar_material(codigo_material):
    materiais = carregar_materiais()
    material = next((m for m in materiais if str(m['codigo']) == str(codigo_material)), None)

    if not material:
        flash('Material não encontrado.', 'danger')
        return redirect(url_for('gerenciar_materiais_escritorio'))

    if request.method == 'POST':
        nova_descricao = request.form.get('descricao', '').strip()
        material['descricao'] = nova_descricao

        if material.get('categoria') == 'epi':
            # Atualiza apenas a Quantidade Máxima para EPI
            qtd_max_raw = request.form.get('quantidade_maxima', '1')
            try:
                material['quantidade_maxima'] = int(qtd_max_raw)
            except ValueError:
                material['quantidade_maxima'] = 1
            
            # Garante que não haverá chave de estoque_minimo no item EPI
            material.pop('estoque_minimo', None)
            
            salvar_materiais(materiais)
            flash('EPI atualizado com sucesso!', 'success')
            return redirect(url_for('gerenciar_materiais_epi'))
        else:
            # Atualiza apenas o Estoque Mínimo para Material de Escritório
            est_min_raw = request.form.get('estoque_minimo', '0')
            try:
                material['estoque_minimo'] = int(est_min_raw)
            except ValueError:
                material['estoque_minimo'] = 0

            # Remove quantidade_maxima se ainda existia do modelo antigo
            material.pop('quantidade_maxima', None)

            salvar_materiais(materiais)
            flash('Material de escritório atualizado com sucesso!', 'success')
            return redirect(url_for('gerenciar_materiais_escritorio'))

    return render_template('editar_material.html', material=material)

@app.route('/gerenciar_departamentos', methods=['GET', 'POST'])
@login_required
@admin_required
def gerenciar_departamentos():
    if request.method == 'POST':
        nome_departamento = request.form.get('nome', '').strip()
        departamentos = carregar_departamentos()

        if nome_departamento and nome_departamento not in departamentos:
            departamentos.append(nome_departamento)
            salvar_departamentos(departamentos)
            flash(f'Departamento "{nome_departamento}" adicionado com sucesso!', 'success')
        else:
            flash('Departamento já existe ou nome inválido.', 'warning')
            
        return redirect(url_for('gerenciar_departamentos'))

    departamentos = carregar_departamentos()
    return render_template('gerenciar_departamentos.html', departamentos=departamentos)


@app.route('/excluir_departamento', methods=['POST'])
@login_required
@admin_required
def excluir_departamento():
    nome_excluir = request.form.get('nome')
    departamentos = carregar_departamentos()

    if nome_excluir in departamentos:
        departamentos.remove(nome_excluir)
        salvar_departamentos(departamentos)
        flash(f'Departamento "{nome_excluir}" removido.', 'success')

    return redirect(url_for('gerenciar_departamentos'))


@app.route('/requisicao/<int:requisicao_id>')
@login_required
def ver_requisicao(requisicao_id):
    requisicoes = carregar_requisicoes()
    requisicao = next((r for r in requisicoes if r['id'] == requisicao_id), None)
    
    if not requisicao:
        flash('Requisição não encontrada.', 'danger')
        return redirect(url_for('historico'))
    
    usuario_logado = session['user']
    registro_logado = str(usuario_logado.get('registro', '')).strip().lower()
    permissao_logada = usuario_logado.get('permissao', '')
    
    registro_requisicao = str(requisicao.get('registro', '')).strip().lower()
    eh_dono = (registro_requisicao == registro_logado)
    
    if permissao_logada not in ['admin', 'administrador', 'abastecedor'] and not eh_dono:
        flash('Você não tem permissão para visualizar esta requisição.', 'danger')
        return redirect(url_for('historico'))

    return render_template('ver_requisicao.html', requisicao=requisicao)


@app.route('/importar_materiais_csv', methods=['POST'])
@login_required
@gestor_required
def importar_materiais_csv():
    categoria = request.form.get('categoria', 'escritorio')
    file = request.files.get('arquivo_csv')

    if not file or not file.filename:
        flash('Nenhum arquivo foi selecionado.', 'danger')
        return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

    filename = file.filename.lower()
    materiais = carregar_materiais()
    codigos_existentes = {str(m['codigo']).strip().lower() for m in materiais}
    novos_cadastrados = 0

    try:
        if filename.endswith('.xlsx'):
            workbook = openpyxl.load_workbook(file, data_only=True)
            sheet = workbook.active
            rows = list(sheet.iter_rows(values_only=True))

            if not rows:
                flash('A planilha enviada está vazia.', 'warning')
                return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

            header = [normalizar_texto(cell) for cell in rows[0]]

            idx_codigo = next((i for i, h in enumerate(header) if 'cod' in h), 0)
            idx_desc = next((i for i, h in enumerate(header) if 'desc' in h or 'nome' in h or 'item' in h), 1)
            idx_qtd = next((i for i, h in enumerate(header) if 'qtd' in h or 'quant' in h or 'max' in h), 2)

            for row in rows[1:]:
                if not row or len(row) <= idx_codigo:
                    continue
                
                codigo_raw = row[idx_codigo]
                if codigo_raw is None:
                    continue

                codigo = str(codigo_raw).strip()
                descricao = str(row[idx_desc]).strip() if len(row) > idx_desc and row[idx_desc] is not None else ""
                qtd_max_raw = row[idx_qtd] if len(row) > idx_qtd and row[idx_qtd] is not None else 1

                try:
                    qtd_max = int(float(str(qtd_max_raw).replace(',', '.')))
                except ValueError:
                    qtd_max = 1

                if codigo and descricao and (codigo.lower() not in codigos_existentes):
                    materiais.append({
                        "codigo": codigo,
                        "descricao": descricao,
                        "quantidade_maxima": qtd_max,
                        "categoria": categoria
                    })
                    codigos_existentes.add(codigo.lower())
                    novos_cadastrados += 1

        elif filename.endswith('.csv'):
            content = file.stream.read().decode("utf-8-sig", errors="ignore")
            delimiter = ';' if ';' in content else ','
            stream = io.StringIO(content)
            reader = csv.reader(stream, delimiter=delimiter)
            rows = list(reader)

            if not rows:
                flash('O arquivo CSV está vazio.', 'warning')
                return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

            header = [normalizar_texto(cell) for cell in rows[0]]

            idx_codigo = next((i for i, h in enumerate(header) if 'cod' in h), 0)
            idx_desc = next((i for i, h in enumerate(header) if 'desc' in h or 'nome' in h or 'item' in h), 1)
            idx_qtd = next((i for i, h in enumerate(header) if 'qtd' in h or 'quant' in h or 'max' in h), 2)

            for row in rows[1:]:
                if not row or len(row) <= idx_codigo:
                    continue

                codigo = str(row[idx_codigo]).strip()
                descricao = str(row[idx_desc]).strip() if len(row) > idx_desc else ""
                qtd_max_raw = row[idx_qtd] if len(row) > idx_qtd else 1

                try:
                    qtd_max = int(float(str(qtd_max_raw).replace(',', '.')))
                except ValueError:
                    qtd_max = 1

                if codigo and descricao and (codigo.lower() not in codigos_existentes):
                    materiais.append({
                        "codigo": codigo,
                        "descricao": descricao,
                        "quantidade_maxima": qtd_max,
                        "categoria": categoria
                    })
                    codigos_existentes.add(codigo.lower())
                    novos_cadastrados += 1

        else:
            flash('Formato inválido. Por favor, envie um arquivo .csv ou .xlsx (Excel).', 'danger')
            return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

        salvar_materiais(materiais)
        flash(f'Sucesso! {novos_cadastrados} novos materiais foram importados.', 'success')

    except Exception as e:
        flash(f'Erro ao processar o arquivo: {str(e)}', 'danger')

    return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))


if __name__ == '__main__':
    app.run(debug=True)